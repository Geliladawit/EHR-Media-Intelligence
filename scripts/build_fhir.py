"""Build FHIR R4 Bundles from the cleaned IR and store them in SQLite.

Usage: python scripts/build_fhir.py
Reads:  data/clean/{patients,records}.json   (produced by the Task 1 pipeline)
Writes: data/fhir/validation_report.json, ehr_media.db (bundles table)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ehr_media.fhir.bundles import build_all_bundles
from ehr_media.fhir.store import BundleStore, write_validation_report
from ehr_media.ingestion.models import CleanBatch, ClinicalRecord, PatientDemographics

CLEAN_DIR = ROOT / "data" / "clean"
DB_PATH = ROOT / "ehr_media.db"
REPORT_PATH = ROOT / "data" / "fhir" / "validation_report.json"


def load_clean_batch(clean_dir: Path = CLEAN_DIR) -> CleanBatch:
    patients_raw = json.loads((clean_dir / "patients.json").read_text(encoding="utf-8"))
    records_raw = json.loads((clean_dir / "records.json").read_text(encoding="utf-8"))
    return CleanBatch(
        patients={k: PatientDemographics.model_validate(v) for k, v in patients_raw.items()},
        records=[ClinicalRecord.model_validate(r) for r in records_raw],
        audit_log=[],
    )


def main() -> None:
    batch = load_clean_batch()
    bundles, errors = build_all_bundles(batch)

    store = BundleStore(DB_PATH)
    for patient_key, bundle in bundles.items():
        store.upsert_bundle(patient_key, bundle)
    store.close()

    report_path = write_validation_report(errors, REPORT_PATH)

    total_entries = sum(len(b.get("entry", [])) for b in bundles.values())
    print(f"Patients bundled : {len(bundles)}")
    print(f"Total resources  : {total_entries}")
    print(f"Validation errors: {len(errors)} -> {report_path}")
    print(f"Store            : {DB_PATH}")


if __name__ == "__main__":
    main()
