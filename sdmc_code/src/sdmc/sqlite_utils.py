from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import hashlib
import sqlite3
import time


WRITE_SQL_PREFIXES = {
    "insert", "update", "delete", "create", "drop", "alter", "attach",
    "detach", "vacuum", "replace", "pragma writable_schema",
}


def quote_ident(identifier: str) -> str:
    if "\x00" in identifier:
        raise ValueError("NUL byte in SQLite identifier")
    return '"' + identifier.replace('"', '""') + '"'


def assert_read_only_sql(sql: str) -> None:
    prefix = sql.strip().lower().split(None, 1)[0] if sql.strip() else ""
    if prefix in WRITE_SQL_PREFIXES:
        raise ValueError(f"write SQL is forbidden in SDMC: {prefix}")
    lowered = sql.strip().lower()
    if lowered.startswith("pragma ") and not lowered.startswith(("pragma table_info", "pragma foreign_key_list")):
        raise ValueError(f"write/unsafe PRAGMA is forbidden: {sql[:80]}")


@contextmanager
def open_sqlite_readonly(path: str | Path, timeout_seconds: float | None = None) -> Iterator[sqlite3.Connection]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    uri = f"file:{p.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    start = time.monotonic()
    if timeout_seconds is not None:
        def progress() -> int:
            return 1 if time.monotonic() - start > timeout_seconds else 0
        conn.set_progress_handler(progress, 1000)
    try:
        yield conn
    finally:
        conn.close()


def _run_with_timeout(conn: sqlite3.Connection, timeout_seconds: float | None, fn):
    if timeout_seconds is None:
        return fn()
    start = time.monotonic()

    def progress() -> int:
        return 1 if time.monotonic() - start > timeout_seconds else 0

    old_handler_set = True
    conn.set_progress_handler(progress, 1000)
    try:
        return fn()
    finally:
        conn.set_progress_handler(None, 0)


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple = (), timeout_seconds: float | None = None) -> list[sqlite3.Row]:
    assert_read_only_sql(sql)
    return _run_with_timeout(conn, timeout_seconds, lambda: list(conn.execute(sql, params)))


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple = (), timeout_seconds: float | None = None) -> sqlite3.Row | None:
    assert_read_only_sql(sql)
    return _run_with_timeout(conn, timeout_seconds, lambda: conn.execute(sql, params).fetchone())


def stable_id(*parts: object) -> str:
    return ":".join(str(p).replace(":", "_") for p in parts if p is not None)


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:length]
