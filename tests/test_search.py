"""Tests for Task 4: semantic search."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ehr_media.fhir.bundles import build_all_bundles
from ehr_media.ingestion.pipeline import ingest_raw
from ehr_media.ingestion.models import RawRecord
from ehr_media.search.indexer import build_index, extract_documents
from ehr_media.search.vectorstore import VectorStore


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    """Build a small real index once for the whole module."""
    raws = [
        RawRecord.model_validate({
            "record_id": "R1", "mrn": "MRN-1", "patient_name": "Jane Doe",
            "dob": "1985-03-14", "gender": "F", "record_type": "lab",
            "record_date": "2024-01-15", "encounter_id": "ENC-1",
            "content": "Troponin I 8.4 ng/mL (H). CK-MB elevated. Serial troponins rising — concerning for ongoing ischemia."}),
        RawRecord.model_validate({
            "record_id": "R2", "mrn": "MRN-1", "patient_name": "Jane Doe",
            "dob": "1985-03-14", "gender": "F", "record_type": "imaging",
            "record_date": "2024-01-16", "encounter_id": "ENC-1",
            "content": "Chest X-ray: no acute cardiopulmonary process. Mild cardiomegaly."}),
        RawRecord.model_validate({
            "record_id": "R3", "mrn": "MRN-2", "patient_name": "John Roe",
            "dob": "1970-07-04", "gender": "M", "record_type": "lab",
            "record_date": "2024-03-01", "encounter_id": "ENC-9",
            "content": "HbA1c 9.2% (H). Fasting glucose 240. New diagnosis diabetes; start metformin."}),
        RawRecord.model_validate({
            "record_id": "R4", "mrn": "MRN-3", "patient_name": "Amy Lin",
            "dob": "1990-01-01", "gender": "F", "record_type": "imaging",
            "record_date": "2024-06-15", "encounter_id": "ENC-5",
            "content": "Mammogram bilateral: no suspicious findings. BI-RADS 1."}),
        RawRecord.model_validate({
            "record_id": "R5", "mrn": "MRN-3", "patient_name": "Amy Lin",
            "dob": "1990-01-01", "gender": "F", "record_type": "clinical_note",
            "record_date": "2024-06-20", "encounter_id": "ENC-5",
            "content": "Progress note: patient reports headaches. Neurology referral placed."}),
    ]
    batch = ingest_raw(raws)
    bundles, errors = build_all_bundles(batch)
    assert errors == []

    # persist bundles into a temp sqlite db in the expected shape
    import json
    import sqlite3
    db = tmp_path_factory.mktemp("search") / "test.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bundles (patient_key TEXT PRIMARY KEY,"
                 " bundle_json TEXT NOT NULL, resource_counts TEXT, created_at TEXT)")
    for k, b in bundles.items():
        conn.execute("INSERT INTO bundles VALUES (?,?,?,?)",
                     (k, json.dumps(b), "{}", "2024-01-01"))
    conn.commit()
    conn.close()

    index_path = tmp_path_factory.mktemp("idx") / "index.faiss"
    stats = build_index(db, index_path)
    return db, index_path, stats


def _store(db, idx):
    return VectorStore(db, idx)


def test_index_built(indexed):
    db, idx, stats = indexed
    assert stats["documents_seen"] == 5
    assert stats["documents_added"] == 5
    assert stats["index_vectors"] == 5


def test_semantic_ranking(indexed):
    db, idx, _ = indexed
    store = _store(db, idx)
    hits = store.search("heart attack cardiac ischemia", top_k=5)
    assert hits
    # troponin lab record should outrank mammogram/neurology
    top = hits[0]
    assert top["record_id"] in ("R1", "R2")
    assert 0.0 <= top["score"] <= 1.0
    store.close()


def test_resource_type_filter(indexed):
    db, idx, _ = indexed
    store = _store(db, idx)
    hits = store.search("report findings", top_k=5, resource_type="imaging")
    assert hits
    assert all(h["resource_type"] == "imaging" for h in hits)
    store.close()


def test_date_range_filter(indexed):
    db, idx, _ = indexed
    store = _store(db, idx)
    # only Jan 2024 records: R1, R2
    hits = store.search("patient record", top_k=10,
                        date_from="2024-01-01", date_to="2024-01-31")
    assert hits
    assert all(h["record_date"].startswith("2024-01") for h in hits)
    assert {h["record_id"] for h in hits} <= {"R1", "R2"}
    store.close()


def test_combined_filters(indexed):
    db, idx, _ = indexed
    store = _store(db, idx)
    hits = store.search("diabetes glucose", top_k=5,
                        resource_type="lab", date_from="2024-03-01",
                        date_to="2024-03-31")
    assert len(hits) == 1
    assert hits[0]["record_id"] == "R3"
    store.close()


def test_top_k_respected(indexed):
    db, idx, _ = indexed
    store = _store(db, idx)
    hits = store.search("patient", top_k=2)
    assert len(hits) == 2
    store.close()


def test_latency_under_2s(indexed):
    db, idx, _ = indexed
    store = _store(db, idx)
    start = time.perf_counter()
    store.search("chest pain evaluation", top_k=5)
    elapsed = time.perf_counter() - start
    store.close()
    assert elapsed < 2.0  # expect ~50-200ms


def test_id_join_integrity(indexed):
    db, idx, _ = indexed
    store = _store(db, idx)
    hits = store.search("clinical note", top_k=5)
    import sqlite3
    conn = sqlite3.connect(db)
    for h in hits:
        row = conn.execute(
            "SELECT COUNT(*) FROM embeddings_meta WHERE patient_key=? AND record_id=?",
            (h["patient_key"], h["record_id"])).fetchone()
        assert row[0] == 1
    conn.close()
    store.close()


def test_empty_store_returns_empty(tmp_path):
    store = VectorStore(tmp_path / "e.db", tmp_path / "e.faiss")
    assert store.search("anything") == []
    store.close()


def test_incremental_index_skips_existing(indexed):
    db, idx, _ = indexed
    store = _store(db, idx)
    before = store.stats()["index_vectors"]
    added = store.add_documents([{
        "patient_key": "MRN:X", "record_id": "RX", "resource_type": "lab",
        "record_date": "2024-01-01", "patient_name": "X", "mrn": "X",
        "text": "lab | 2024-01-01 | potassium 6.1 (H)"}])
    assert added == 1
    # same doc again -> skipped
    added2 = store.add_documents([{
        "patient_key": "MRN:X", "record_id": "RX", "resource_type": "lab",
        "record_date": "2024-01-01", "patient_name": "X", "mrn": "X",
        "text": "lab | 2024-01-01 | potassium 6.1 (H)"}])
    assert added2 == 0
    store.close()
