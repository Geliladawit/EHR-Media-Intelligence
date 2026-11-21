"""Persistence for FHIR Bundles and the validation report."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS bundles (
    patient_key TEXT PRIMARY KEY,
    bundle_json TEXT NOT NULL,
    resource_counts TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class BundleStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA)

    def upsert_bundle(self, patient_key: str, bundle: dict) -> None:
        counts: dict[str, int] = {}
        for entry in bundle.get("entry", []):
            rt = entry.get("resource", {}).get("resourceType", "?")
            counts[rt] = counts.get(rt, 0) + 1
        self._conn.execute(
            "INSERT INTO bundles (patient_key, bundle_json, resource_counts, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(patient_key) DO UPDATE SET"
            "   bundle_json=excluded.bundle_json,"
            "   resource_counts=excluded.resource_counts,"
            "   created_at=excluded.created_at",
            (patient_key, json.dumps(bundle), json.dumps(counts),
             datetime.now(timezone.utc).isoformat()))
        self._conn.commit()

    def get_bundle(self, patient_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT bundle_json FROM bundles WHERE patient_key=?",
            (patient_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_patient_keys(self) -> list[str]:
        rows = self._conn.execute("SELECT patient_key FROM bundles").fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self._conn.close()


def write_validation_report(errors: list[dict], out_path: str | Path) -> Path:
    """Persist the FHIR validation report; empty list = all bundles valid."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_errors": len(errors),
        "errors": errors,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out
