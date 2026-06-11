from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from sdmc.config import SDMCConfig
from sdmc.config import read_api_key
from sdmc.graph import materialize_graphs
from sdmc.sqlite_utils import quote_ident, open_sqlite_readonly
from sdmc.stage_a import run_build, run_inventory
from sdmc.stage_b import dry_run_question, evaluate_readonly, extract_sql, leakage_flags
from sdmc.experiment import ExperimentSpec, run_experiment


class SDMCCoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "spider_data"
        db_dir = self.root / "database" / "school"
        db_dir.mkdir(parents=True)
        db = db_dir / "school.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE student (
              student_id INTEGER PRIMARY KEY,
              name TEXT,
              age INTEGER
            );
            CREATE TABLE club (
              club_id INTEGER PRIMARY KEY,
              category TEXT
            );
            CREATE TABLE membership (
              membership_id INTEGER PRIMARY KEY,
              student_id INTEGER,
              club_id INTEGER,
              join_year INTEGER,
              FOREIGN KEY(student_id) REFERENCES student(student_id),
              FOREIGN KEY(club_id) REFERENCES club(club_id)
            );
            INSERT INTO student VALUES (1, 'Alice', 18), (2, 'Bob', 19);
            INSERT INTO club VALUES (1, 'sports'), (2, 'academic');
            INSERT INTO membership VALUES (1, 1, 1, 2021), (2, 2, 2, 2020);
            """
        )
        conn.commit()
        conn.close()
        (self.root / "dev.json").write_text(json.dumps([
            {"db_id": "school", "question": "Which clubs have members after 2020?", "query": "SELECT category FROM club JOIN membership ON club.club_id = membership.club_id WHERE join_year > 2020"}
        ]), encoding="utf-8")
        (self.root / "tables.json").write_text(json.dumps([{
            "db_id": "school",
            "table_names_original": ["student", "club", "membership"],
            "column_names_original": [[-1, "*"], [0, "student_id"], [0, "name"], [0, "age"], [1, "club_id"], [1, "category"], [2, "membership_id"], [2, "student_id"], [2, "club_id"], [2, "join_year"]],
            "primary_keys": [1, 4, 6],
            "foreign_keys": [[7, 1], [8, 4]]
        }]), encoding="utf-8")
        self.out = Path(self.tmp.name) / "outputs"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_quote_and_readonly(self) -> None:
        self.assertEqual(quote_ident('a"b'), '"a""b"')
        with open_sqlite_readonly(self.root / "database" / "school" / "school.sqlite") as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO student VALUES (3, 'Eve', 20)")

    def test_stage_a_inventory_build_graph_and_stage_b_dry_run(self) -> None:
        config = SDMCConfig(output_root=str(self.out))
        inv = run_inventory("spider", "dev", self.root, self.out, config)
        self.assertEqual(len(inv), 1)
        self.assertTrue((self.out / "context_store.sqlite").exists())
        run_build("spider", "dev", self.root, self.out, config)
        materialize_graphs(self.out / "context_store.sqlite")
        result = dry_run_question(self.out / "context_store.sqlite", "school", "Which clubs have members after 2020?", config)
        self.assertIn("SDMC", result["conditions"])
        self.assertFalse(result["conditions"]["SDMC"]["leakage_flags"])
        self.assertGreater(result["conditions"]["SDMC"]["estimated_token_count"], 10)

    def test_batch_experiment_dry_run(self) -> None:
        config = SDMCConfig(output_root=str(self.out))
        run_build("spider", "dev", self.root, self.out, config)
        from sdmc.graph import materialize_graphs
        materialize_graphs(self.out / "context_store.sqlite")
        exp_out = Path(self.tmp.name) / "exp"
        spec = ExperimentSpec(
            dataset="spider",
            split="dev",
            root=str(self.root),
            store=str(self.out / "context_store.sqlite"),
            output_dir=str(exp_out),
            conditions=["RAW_SCHEMA", "C1", "SDMC"],
            limit=1,
        )
        result = run_experiment(spec, config, dry_run=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue((exp_out / "prompt_records.jsonl").exists())
        self.assertTrue((exp_out / "per_question_results.csv").exists())

    def test_sql_extractor_and_leakage(self) -> None:
        self.assertEqual(extract_sql("```sql\nSELECT * FROM t\n```"), "SELECT * FROM t")
        self.assertEqual(extract_sql("SELECT * FROM t;\nExplanation: done"), "SELECT * FROM t")
        self.assertIn("gold_sql_leak", leakage_flags("SELECT * FROM t", gold_sql="SELECT * FROM t"))

    def test_evaluate_readonly_normalizes_mixed_result_types(self) -> None:
        db = self.root / "database" / "school" / "school.sqlite"
        result = evaluate_readonly(
            db,
            "SELECT name, age FROM student",
            "SELECT name, age FROM student ORDER BY age DESC",
        )
        self.assertEqual(result["execution_status"], "success")
        self.assertTrue(result["execution_match"])

    def test_api_key_parser_ignores_description_text(self) -> None:
        path = Path(self.tmp.name) / "<API_KEY_FILE>"
        path.write_text("deepseek api,\n\napi_key = 'dummy-test-key-123'\n", encoding="utf-8")
        self.assertEqual(read_api_key(path), "dummy-test-key-123")


if __name__ == "__main__":
    unittest.main()
