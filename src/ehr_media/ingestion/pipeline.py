"""Ingestion pipeline orchestration.

Reads one or more export files (JSON / CSV / plain-text), cleans and
normalizes them, resolves patient identity across files, deduplicates,
and produces a validated CleanBatch plus an audit trail.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from .cleaning import clean_records
from .models import AuditEntry, CleanBatch, ClinicalRecord, RawRecord
from .readers import read_export


def _record_dedup_hash(record: ClinicalRecord) -> str:
    basis = (f"{record.patient_key}|{record.record_type.value}|"
             f"{record.record_date.isoformat()}|{record.content.strip().lower()}")
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def ingest_files(paths: Iterable[str | Path]) -> CleanBatch:
    """Ingest and clean multiple export files into a single CleanBatch."""
    all_patients: dict = {}
    all_records: list[ClinicalRecord] = []
    all_audit: list[AuditEntry] = []
    all_dropped: list[dict] = []
    seen: set[str] = set()

    for path in paths:
        path = Path(path)
        raw_records = read_export(path)
        batch = clean_records(raw_records, source_file=str(path))

        all_patients.update(batch.patients)
        all_audit.extend(batch.audit_log)
        all_dropped.extend(batch.dropped_records)

        for record in batch.records:
            h = _record_dedup_hash(record)
            if h in seen:
                all_dropped.append({"record_id": record.record_id,
                                    "reason": f"cross-file duplicate ({path.name})"})
                all_audit.append(AuditEntry(
                    record_id=record.record_id, field="*", rule="dedup",
                    old_value=None, new_value="cross-file duplicate"))
                continue
            seen.add(h)
            all_records.append(record)

    return CleanBatch(patients=all_patients, records=all_records,
                      audit_log=all_audit, dropped_records=all_dropped)


def ingest_raw(raw_records: list[RawRecord], source_file: str = "<memory>") -> CleanBatch:
    """Clean an in-memory list of RawRecords (used by tests)."""
    return clean_records(raw_records, source_file=source_file)


def write_clean_output(batch: CleanBatch, out_dir: str | Path) -> dict[str, Path]:
    """Persist the clean IR and audit log as JSON for inspection / Task 2 input."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    patients_path = out / "patients.json"
    records_path = out / "records.json"
    audit_path = out / "audit_log.jsonl"
    dropped_path = out / "dropped.json"

    patients_path.write_text(
        json.dumps({k: v.model_dump(mode="json") for k, v in batch.patients.items()},
                   indent=2), encoding="utf-8")
    records_path.write_text(
        json.dumps([r.model_dump(mode="json") for r in batch.records],
                   indent=2), encoding="utf-8")
    with open(audit_path, "w", encoding="utf-8") as fh:
        for entry in batch.audit_log:
            fh.write(json.dumps(entry.model_dump(mode="json")) + "\n")
    dropped_path.write_text(json.dumps(batch.dropped_records, indent=2),
                            encoding="utf-8")

    return {"patients": patients_path, "records": records_path,
            "audit_log": audit_path, "dropped": dropped_path}
