"""FHIR Bundle assembly and R4B schema validation.

Each patient gets one Bundle (type="collection") containing their Patient,
Encounters, DocumentReferences, and DiagnosticReports, with deterministic
uuid5 fullUrls so references resolve identically on every run.
"""

from __future__ import annotations

import uuid
from typing import Any

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.patient import Patient

from ehr_media.ingestion.models import (
    CleanBatch,
    ClinicalRecord,
    PatientDemographics,
    RecordType,
)

from .mappers import (
    diagnostic_report_payload,
    document_reference_payload,
    encounter_payload,
    patient_payload,
)

_RESOURCE_VALIDATORS = {
    "Patient": Patient,
    "Encounter": Encounter,
    "DocumentReference": DocumentReference,
    "DiagnosticReport": DiagnosticReport,
}


def full_url(patient_key: str, *parts: str) -> str:
    """Deterministic, RFC-4122-valid urn:uuid fullUrl."""
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_DNS, '|'.join((patient_key, *parts)))}"


def _encounter_key(record: ClinicalRecord, patient_key: str) -> str:
    """Group records by encounter_id; blank IDs get a per-day synthetic key."""
    if record.encounter_id:
        return record.encounter_id
    return f"AUTO-{patient_key}-{record.record_date.isoformat()}"


def assemble_patient_bundle(
    patient_key: str,
    demo: PatientDemographics,
    records: list[ClinicalRecord],
) -> tuple[dict, list[dict]]:
    """Build an unvalidated Bundle payload for one patient.

    Returns (bundle_payload, member_payloads); member_payloads is the flat
    list of resource dicts (same objects referenced inside bundle entries).
    """
    patient_url = full_url(patient_key, "Patient")

    # Group records into encounters
    enc_groups: dict[str, list[ClinicalRecord]] = {}
    for rec in records:
        enc_groups.setdefault(_encounter_key(rec, patient_key), []).append(rec)

    # Patient
    patient = patient_payload(demo, patient_url)
    entries: list[dict] = [{"fullUrl": patient_url, "resource": patient}]
    members: list[dict] = [patient]

    # Encounters (sorted for deterministic ordering)
    encounter_urls: dict[str, str] = {}
    for enc_key in sorted(enc_groups):
        recs = enc_groups[enc_key]
        earliest = min(r.record_date for r in recs)
        url = full_url(patient_key, "Encounter", enc_key)
        encounter_urls[enc_key] = url
        enc = encounter_payload(enc_key, patient_url, earliest, url)
        entries.append({"fullUrl": url, "resource": enc})
        members.append(enc)

    # DocumentReferences (every record) + DiagnosticReports (lab/imaging)
    for rec in records:
        enc_url = encounter_urls[_encounter_key(rec, patient_key)]

        doc_url = full_url(patient_key, "DocumentReference", rec.record_id)
        doc = document_reference_payload(rec, patient_url, enc_url, doc_url)
        entries.append({"fullUrl": doc_url, "resource": doc})
        members.append(doc)

        if rec.record_type in (RecordType.LAB, RecordType.IMAGING):
            report_url = full_url(patient_key, "DiagnosticReport", rec.record_id)
            report = diagnostic_report_payload(rec, patient_url, enc_url, report_url)
            entries.append({"fullUrl": report_url, "resource": report})
            members.append(report)

    bundle = {"resourceType": "Bundle", "type": "collection", "entry": entries}
    return bundle, members


def build_all_bundles(batch: CleanBatch) -> tuple[dict[str, dict], list[dict]]:
    """Assemble + schema-validate one Bundle per patient.

    Returns (bundles_by_patient_key, validation_errors). Errors are collected,
    never raised — a bad resource doesn't sink the whole batch.
    """
    bundles: dict[str, dict] = {}
    errors: list[dict] = []

    records_by_patient: dict[str, list[ClinicalRecord]] = {}
    for rec in batch.records:
        records_by_patient.setdefault(rec.patient_key, []).append(rec)

    for patient_key, demo in batch.patients.items():
        records = records_by_patient.get(patient_key, [])
        bundle_payload, members = assemble_patient_bundle(
            patient_key, demo, records)

        for member in members:
            validator = _RESOURCE_VALIDATORS.get(member["resourceType"])
            if validator is None:
                continue
            try:
                validator.validate(member)
            except Exception as exc:  # pydantic/FHIR validation errors
                errors.append({
                    "patient_key": patient_key,
                    "resource_type": member["resourceType"],
                    "resource_id": member.get("id"),
                    "error": str(exc),
                })

        try:
            Bundle.validate(bundle_payload)
        except Exception as exc:
            errors.append({
                "patient_key": patient_key,
                "resource_type": "Bundle",
                "resource_id": patient_key,
                "error": str(exc),
            })

        bundles[patient_key] = bundle_payload

    return bundles, errors
