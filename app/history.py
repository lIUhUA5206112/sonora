"""Converted-file history (SQLite). Lets a restart show finished tracks as done
instead of re-encoding them. Keyed by source path + mtime + size + target format."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from . import config


def _connect() -> sqlite3.Connection:
    config.DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATA / "history.db", timeout=5)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS done(
               path TEXT NOT NULL,
               mtime_ns INTEGER NOT NULL,
               size INTEGER NOT NULL,
               target TEXT NOT NULL,
               output TEXT NOT NULL,
               ts REAL NOT NULL,
               peaks TEXT,
               PRIMARY KEY(path, target)
           )"""
    )
    try:
        conn.execute("ALTER TABLE done ADD COLUMN peaks TEXT")
    except sqlite3.OperationalError:
        pass
    return conn


def get(path: str, mtime_ns: int, size: int, target: str) -> tuple[str, list[int] | None] | None:
    try:
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT output, peaks FROM done WHERE path=? AND mtime_ns=? AND size=? AND target=?",
                (path, mtime_ns, size, target),
            ).fetchone()
        if not row:
            return None
        peaks = json.loads(row[1]) if row[1] else None
        return row[0], peaks
    except (sqlite3.Error, ValueError):
        return None


def put(path: str, mtime_ns: int, size: int, target: str, output: str,
        peaks: list[int] | None = None) -> None:
    try:
        with closing(_connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO done VALUES(?,?,?,?,?,?,?)",
                (path, mtime_ns, size, target, output, time.time(),
                 json.dumps(peaks) if peaks else None),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def forget(path: str) -> None:
    try:
        with closing(_connect()) as conn:
            conn.execute("DELETE FROM done WHERE path=?", (path,))
            conn.commit()
    except sqlite3.Error:
        pass
