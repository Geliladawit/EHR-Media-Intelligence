"""SQLite cache for AI summaries.

Keyed by patient_key; a hit requires the stored record_hash to match the
current one, so re-runs on unchanged data make zero API calls while any
record change triggers regeneration.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (
    patient_key TEXT PRIMARY KEY,
    record_hash TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    model TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
"""


class SummaryCache:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def get(self, patient_key: str, record_hash: str) -> dict | None:
        """Return cached summary only if the record hash still matches."""
        row = self._conn.execute(
            "SELECT summary_json FROM summaries WHERE patient_key=? AND record_hash=?",
            (patient_key, record_hash)).fetchone()
        return self._loads(row[0]) if row else None

    def put(self, summary: Any, record_hash: str) -> None:
        """Upsert a ClinicalSummary (pydantic model or dict)."""
        data = summary.model_dump(mode="json") if hasattr(summary, "model_dump") else dict(summary)
        patient_key = data["patient_key"]
        self._conn.execute(
            "INSERT INTO summaries (patient_key, record_hash, summary_json, model, generated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(patient_key) DO UPDATE SET"
            "   record_hash=excluded.record_hash,"
            "   summary_json=excluded.summary_json,"
            "   model=excluded.model,"
            "   generated_at=excluded.generated_at",
            (patient_key, record_hash, _dumps(data),
             data.get("model", "unknown"),
             datetime.now(timezone.utc).isoformat()))
        self._conn.commit()

    def list_keys(self) -> list[str]:
        rows = self._conn.execute("SELECT patient_key FROM summaries").fetchall()
        return [r[0] for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]

    @staticmethod
    def _loads(raw: str) -> dict:
        import json
        return json.loads(raw)

    def close(self) -> None:
        self._conn.close()


def _dumps(data: dict) -> str:
    import json
    return json.dumps(data)
