"""Unit tests for Task 1: data ingestion & cleaning."""

from datetime import date

import pytest

from ehr_media.ingestion.cleaning import (
    clean_records,
    normalize_date,
    normalize_gender,
    normalize_mrn,
    normalize_record_type,
)
from ehr_media.ingestion.models import RawRecord, RecordType
from ehr_media.ingestion.pipeline import ingest_raw


def raw(**kwargs) -> RawRecord:
    base = {
        "record_id": "R1",
        "mrn": "MRN-000123",
        "patient_name": "Jane Doe",
        "dob": "1985-03-14",
        "gender": "F",
        "record_type": "lab",
        "record_date": "2024-01-15",
        "content": "CBC normal.",
    }
    base.update(kwargs)
    return RawRecord.model_validate(base)


# --- Edge case 1: inconsistent date formats ---------------------------------

@pytest.mark.parametrize("text,expected", [
    ("2024-01-15", date(2024, 1, 15)),
    ("01/15/2024", date(2024, 1, 15)),
    ("15-Jan-2024", date(2024, 1, 15)),
    ("2024/01/15", date(2024, 1, 15)),
    ("15 Jan 2024", date(2024, 1, 15)),
    ("20240115", date(2024, 1, 15)),
    ("not a date", None),
    ("", None),
    (None, None),
])
def test_normalize_date_formats(text, expected):
    assert normalize_date(text) == expected


def test_date_normalization_audited():
    batch = ingest_raw([raw(record_date="01/15/2024")])
    date_entries = [a for a in batch.audit_log if a.field == "record_date"]
    assert len(date_entries) == 1
    assert date_entries[0].new_value == "2024-01-15"
    assert batch.records[0].record_date == date(2024, 1, 15)


# --- Edge case 2: duplicate records ------------------------------------------

def test_exact_duplicates_dropped():
    batch = ingest_raw([
        raw(record_id="R1"),
        raw(record_id="R2"),  # identical content/date/type/patient
    ])
    assert len(batch.records) == 1
    assert any(d["reason"] == "duplicate record" for d in batch.dropped_records)
    assert any(a.rule == "dedup" for a in batch.audit_log)


def test_same_content_different_date_kept():
    batch = ingest_raw([
        raw(record_id="R1", record_date="2024-01-15"),
        raw(record_id="R2", record_date="2024-02-15"),
    ])
    assert len(batch.records) == 2


# --- Edge case 3: conflicting patient identifiers ----------------------------

def test_conflicting_dob_across_records_audited():
    batch = ingest_raw([
        raw(record_id="R1", dob="1985-03-14"),
        raw(record_id="R2", dob="01/15/1980", record_type="imaging",
            content="CXR clear.", record_date="2024-03-01"),
    ])
    # Same MRN -> same patient key, conflict logged, first DOB retained
    assert len(batch.patients) == 1
    conflict = [a for a in batch.audit_log if a.rule == "identifier_conflict"]
    assert len(conflict) == 1
    assert conflict[0].field == "patient.dob"


def test_missing_mrn_falls_back_to_name_dob():
    batch = ingest_raw([
        raw(record_id="R1", mrn=None),
        raw(record_id="R2", mrn=None, record_type="imaging",
            content="MRI brain.", record_date="2024-04-01"),
    ])
    assert len(batch.patients) == 1
    key = batch.records[0].patient_key
    assert key.startswith("NAME:") and "DOB:1985-03-14" in key


def test_missing_mrn_and_name_drops_record():
    batch = ingest_raw([raw(record_id="R1", mrn=None, patient_name=None, name=None)])
    assert len(batch.records) == 0
    assert batch.dropped_records[0]["reason"] == "missing patient identity"


# --- Edge case 4: missing/unusable fields ------------------------------------

def test_missing_content_drops_record():
    batch = ingest_raw([raw(record_id="R1", content=None, note=None, text=None,
                            clinical_note=None)])
    assert len(batch.records) == 0
    assert "missing content" in batch.dropped_records[0]["reason"]


def test_missing_date_drops_record():
    batch = ingest_raw([raw(record_id="R1", record_date=None, date=None)])
    assert len(batch.records) == 0
    assert "record_date" in batch.dropped_records[0]["reason"]


def test_blank_gender_normalizes_to_unknown():
    batch = ingest_raw([raw(record_id="R1", gender="")])
    patient = batch.patients[batch.records[0].patient_key]
    assert patient.gender.value == "U"


# --- Normalizer unit checks ---------------------------------------------------

def test_normalize_mrn_strips_formatting():
    assert normalize_mrn("mrn-000123") == "MRN000123"
    assert normalize_mrn("MRN 000123") == "MRN000123"
    assert normalize_mrn("  ") is None
    assert normalize_mrn(None) is None


def test_normalize_gender_variants():
    assert normalize_gender("male").value == "M"
    assert normalize_gender("F").value == "F"
    assert normalize_gender("").value == "U"
    assert normalize_gender("nonbinary").value == "U"


def test_normalize_record_type_synonyms():
    assert normalize_record_type("xray") == RecordType.IMAGING
    assert normalize_record_type("discharge") == RecordType.DISCHARGE_SUMMARY
    assert normalize_record_type("lab_result") == RecordType.LAB
    assert normalize_record_type("nonsense") is None


def test_validated_output_models():
    batch = ingest_raw([raw()])
    # Round-trip through pydantic serialization
    dumped = batch.model_dump(mode="json")
    assert dumped["records"][0]["record_date"] == "2024-01-15"
