"""Map cleaned EHR records to FHIR R4 resource payloads (dicts).

All functions return plain dicts in FHIR JSON shape; validation against the
R4B schema happens in bundles.py via fhir.resources .validate().
"""

from __future__ import annotations

import base64
from datetime import date

from ehr_media.ingestion.models import (
    ClinicalRecord,
    Gender,
    PatientDemographics,
    RecordType,
)

FHIR_GENDER = {
    Gender.MALE: "male",
    Gender.FEMALE: "female",
    Gender.UNKNOWN: "unknown",
}

MRN_SYSTEM = "http://ehr-media.local/mrn"


def _fhir_datetime(d: date) -> str:
    """FHIR dateTime requires a time component; date-only is invalid."""
    return f"{d.isoformat()}T00:00:00Z"


def _split_name(full_name: str) -> tuple[str | None, list[str]]:
    """Split a display name into (family, given[]). Handles 'Last, First'."""
    name = full_name.strip()
    if not name or name == "Unknown":
        return None, []
    if "," in name:
        family, _, given_part = name.partition(",")
        given = [p for p in given_part.replace(".", " ").split() if p]
        fam = family.strip() or None
        return fam, given
    parts = name.split()
    if len(parts) == 1:
        return parts[0], []
    return parts[-1], parts[:-1]


def patient_payload(demo: PatientDemographics, full_url: str) -> dict:
    family, given = _split_name(demo.full_name)
    name_entry: dict = {"use": "official"}
    if family:
        name_entry["family"] = family
    if given:
        name_entry["given"] = given
    payload: dict = {
        "resourceType": "Patient",
        "id": full_url.rsplit(":", 1)[-1],
        "identifier": [],
        "name": [name_entry] if name_entry else [],
        "gender": FHIR_GENDER.get(demo.gender, "unknown"),
    }
    if demo.mrn:
        payload["identifier"].append({"system": MRN_SYSTEM, "value": demo.mrn})
    if demo.dob:
        payload["birthDate"] = demo.dob.isoformat()
    return payload


def encounter_payload(encounter_key: str, patient_ref: str,
                      period_start: date | None, full_url: str) -> dict:
    payload: dict = {
        "resourceType": "Encounter",
        "id": full_url.rsplit(":", 1)[-1],
        "status": "finished",
        "class": {"code": "AMB"},
        "subject": {"reference": patient_ref},
    }
    if period_start:
        payload["period"] = {"start": _fhir_datetime(period_start)}
    return payload


def _attachment(content: str, title: str) -> dict:
    return {
        "contentType": "text/plain",
        "title": title,
        "data": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }


def document_reference_payload(record: ClinicalRecord, patient_ref: str,
                               encounter_ref: str, full_url: str) -> dict:
    """Every clinical record -> DocumentReference (the media/document itself)."""
    payload: dict = {
        "resourceType": "DocumentReference",
        "id": full_url.rsplit(":", 1)[-1],
        "status": "current",
        "type": {"text": record.record_type.value},
        "subject": {"reference": patient_ref},
        "date": _fhir_datetime(record.record_date),
        "content": [{"attachment": _attachment(record.content, record.record_id)}],
        "context": {"encounter": [{"reference": encounter_ref}]},
    }
    if record.author:
        payload["author"] = [{"display": record.author}]
    if record.encounter_id:
        payload["context"]["encounter"].append({"display": record.encounter_id})
    return payload


def diagnostic_report_payload(record: ClinicalRecord, patient_ref: str,
                              encounter_ref: str, full_url: str) -> dict | None:
    """Structured results (lab/imaging) -> DiagnosticReport. Others -> None."""
    if record.record_type not in (RecordType.LAB, RecordType.IMAGING):
        return None
    payload: dict = {
        "resourceType": "DiagnosticReport",
        "id": full_url.rsplit(":", 1)[-1],
        "status": "final",
        "code": {"text": record.record_type.value},
        "subject": {"reference": patient_ref},
        "encounter": {"reference": encounter_ref},
        "effectiveDateTime": _fhir_datetime(record.record_date),
        "issued": _fhir_datetime(record.record_date),
        "conclusion": record.content[:4000],
    }
    if record.author:
        payload["performer"] = [{"display": record.author}]
    return payload
