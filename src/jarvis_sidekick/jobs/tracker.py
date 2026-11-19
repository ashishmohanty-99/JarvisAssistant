"""SQLite record of every application: prevents duplicates and enforces a daily cap."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from jarvis_sidekick import paths

_SCHEMA = """CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY, url TEXT, title TEXT, company TEXT, score REAL,
    status TEXT, detail TEXT, updated_at TEXT)"""


class Tracker:
    def __init__(self, path: Path | None = None) -> None:
        self._db = sqlite3.connect(str(path or paths.home() / "applications.db"))
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)

    def record(self, job_id: str, *, url: str, title: str, company: str, score: float, status: str,
               detail: str = "") -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._db:
            self._db.execute(
                "INSERT INTO applications VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                "status=excluded.status, detail=excluded.detail, score=excluded.score, updated_at=excluded.updated_at",
                (job_id, url, title, company, score, status, detail, now),
            )

    def get(self, job_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def submitted_today(self) -> int:
        today = datetime.now(UTC).date().isoformat()
        query = "SELECT COUNT(*) FROM applications WHERE status='submitted' AND updated_at LIKE ?"
        return self._db.execute(query, (today + "%",)).fetchone()[0]

    def all(self) -> list[dict]:
        return [dict(r) for r in self._db.execute("SELECT * FROM applications ORDER BY updated_at DESC")]

    def close(self) -> None:
        self._db.close()
