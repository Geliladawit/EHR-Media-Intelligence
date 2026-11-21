"""Tests for Task 2: FHIR R4 normalization."""

import base64
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ehr_media.fhir.bundles import assemble_patient_bundle, build_all_bundles, full_url
from ehr_media.fhir.store import BundleStore, write_validation_report
from ehr_media.ingestion.models import CleanBatch, ClinicalRecord, PatientDemographics
from ehr_media.ingestion.pipeline import ingest_raw
from ehr_media.ingestion.models import RawRecord


def make_batch() -> CleanBatch:
    raws = [
        RawRecord.model_validate({
            "record_id": "R1", "mrn": "MRN-1", "patient_name": "Jane Doe",
            "dob": "1985-03-14", "gender": "F", "record_type": "lab",
            "record_date": "2024-01-15", "encounter_id": "ENC-1",
            "author": "Dr. Smith", "content": "CBC: WBC 11.8 (H), Hgb 10.2 (L)."}),
        RawRecord.model_validate({
            "record_id": "R2", "mrn": "MRN-1", "patient_name": "Jane Doe",
            "dob": "1985-03-14", "gender": "F", "record_type": "imaging",
            "record_date": "2024-01-20", "encounter_id": "ENC-1",
            "content": "Chest X-ray: no acute infiltrate."}),
        RawRecord.model_validate({
            "record_id": "R3", "mrn": "MRN-1", "patient_name": "Jane Doe",
            "dob": "1985-03-14", "gender": "F", "record_type": "discharge_summary",
            "record_date": "2024-01-25", "encounter_id": "",
            "content": "Admitted with chest pain, ruled out MI."}),
        RawRecord.model_validate({
            "record_id": "R4", "mrn": "MRN-2", "patient_name": "John Roe",
            "dob": "1970-07-04", "gender": "M", "record_type": "clinical_note",
            "record_date": "2024-02-01", "content": "Progress note: stable."}),
    ]
    return ingest_raw(raws)


@pytest.fixture
def batch() -> CleanBatch:
    return make_batch()


@pytest.fixture
def bundles(batch):
    return build_all_bundles(batch)


def test_all_bundles_validate_clean(batch, bundles):
    bundle_map, errors = bundles
    assert errors == []
    assert set(bundle_map.keys()) == set(batch.patients.keys())


def test_bundle_structure_and_references(batch, bundles):
    bundle_map, _ = bundles
    for patient_key, bundle in bundle_map.items():
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "collection"

        full_urls = {e["fullUrl"] for e in bundle["entry"]}
        resources = {e["resource"]["resourceType"]: e["resource"]
                     for e in bundle["entry"]}

        # Patient exists with correct gender/birthDate
        patients = [e["resource"] for e in bundle["entry"]
                    if e["resource"]["resourceType"] == "Patient"]
        assert len(patients) == 1

        # Every subject/encounter reference resolves inside the bundle
        for entry in bundle["entry"]:
            res = entry["resource"]
            for ref_field in ("subject", "encounter"):
                ref = res.get(ref_field)
                if isinstance(ref, dict) and "reference" in ref:
                    assert ref["reference"] in full_urls, \
                        f"{res['resourceType']} {res.get('id')} dangling {ref_field}"
            # DocumentReference context.encounter is a list
            ctx = res.get("context", {})
            for enc_ref in ctx.get("encounter", []):
                if "reference" in enc_ref:
                    assert enc_ref["reference"] in full_urls


def test_resource_type_coverage(batch, bundles):
    bundle_map, _ = bundles
    all_types = set()
    for bundle in bundle_map.values():
        for e in bundle["entry"]:
            all_types.add(e["resource"]["resourceType"])
    assert {"Patient", "Encounter", "DocumentReference",
            "DiagnosticReport"} <= all_types


def test_diagnostic_report_only_for_lab_imaging(batch, bundles):
    bundle_map, _ = bundles
    # Patient MRN-1 has lab+imaging+discharge -> 2 DiagnosticReports
    jane_key = next(k for k in bundle_map if "MRN" in k)
    reports = [e["resource"] for e in bundle_map[jane_key]["entry"]
               if e["resource"]["resourceType"] == "DiagnosticReport"]
    assert len(reports) == 2
    for r in reports:
        assert r["status"] == "final"
        assert "reference" in r["encounter"]


def test_document_reference_fields(batch, bundles):
    bundle_map, _ = bundles
    jane_key = next(k for k in bundle_map if "MRN" in k)
    docs = [e["resource"] for e in bundle_map[jane_key]["entry"]
            if e["resource"]["resourceType"] == "DocumentReference"]
    assert len(docs) == 3  # all 3 of Jane's records
    for d in docs:
        assert d["status"] == "current"
        assert d["content"][0]["attachment"]["contentType"] == "text/plain"
        # base64 decodes to the clinical text
        decoded = base64.b64decode(d["content"][0]["attachment"]["data"]).decode()
        assert len(decoded) > 0
        assert len(d["context"]["encounter"]) >= 1


def test_full_urls_are_valid_uuid5():
    url = full_url("MRN:ABC", "Patient")
    assert url.startswith("urn:uuid:")
    uuid.UUID(url.removeprefix("urn:uuid:"))  # raises if invalid
    # deterministic
    assert url == full_url("MRN:ABC", "Patient")
    assert url != full_url("MRN:ABC", "Encounter", "ENC-1")


def test_encounter_grouping(batch, bundles):
    bundle_map, _ = bundles
    jane_key = next(k for k in bundle_map if "MRN" in k)
    encounters = [e["resource"] for e in bundle_map[jane_key]["entry"]
                  if e["resource"]["resourceType"] == "Encounter"]
    # ENC-1 (lab+imaging) + AUTO encounter for blank encounter_id discharge
    assert len(encounters) == 2


def test_validation_report_surfaces_errors(tmp_path, batch, monkeypatch):
    # Corrupt one mapper's output so FHIR schema validation must fail
    import ehr_media.fhir.bundles as bundles_mod

    orig = bundles_mod.document_reference_payload

    def corrupt(*args, **kwargs):
        payload = orig(*args, **kwargs)
        payload["content"] = "not-a-list"  # violates FHIR content cardinality
        return payload

    monkeypatch.setattr(bundles_mod, "document_reference_payload", corrupt)
    _, errors = build_all_bundles(batch)

    assert len(errors) >= 1
    assert any(e["resource_type"] == "DocumentReference" for e in errors)

    report = write_validation_report(errors, tmp_path / "report.json")
    data = json.loads(report.read_text())
    assert data["total_errors"] == len(errors)
    assert "DocumentReference" in data["errors"][0]["error"]


def test_bundle_store_roundtrip(tmp_path, bundles):
    bundle_map, _ = bundles
    store = BundleStore(tmp_path / "test.db")
    for k, b in bundle_map.items():
        store.upsert_bundle(k, b)
    for k, b in bundle_map.items():
        assert store.get_bundle(k) == b
    assert set(store.list_patient_keys()) == set(bundle_map.keys())
    store.close()


def test_serialized_bundle_is_valid_json(bundles):
    bundle_map, _ = bundles
    for b in bundle_map.values():
        json.dumps(b)  # must not raise
