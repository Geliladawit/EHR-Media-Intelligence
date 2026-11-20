"""Canonical intermediate representation for cleaned EHR records.

These models are the validated output of the ingestion pipeline and the
input contract for FHIR mapping (Task 2).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RecordType(str, Enum):
    LAB = "lab"
    IMAGING = "imaging"
    DISCHARGE_SUMMARY = "discharge_summary"
    CLINICAL_NOTE = "clinical_note"


class Gender(str, Enum):
    MALE = "M"
    FEMALE = "F"
    UNKNOWN = "U"


class AuditEntry(BaseModel):
    """One change made to a record during cleaning."""

    record_id: str
    field: str
    rule: str
    old_value: Any = None
    new_value: Any = None


class PatientDemographics(BaseModel):
    """Canonical patient identity, normalized across all their records."""

    patient_key: str = Field(description="Stable join key: canonical MRN or name+DOB fallback")
    mrn: str | None = None
    full_name: str
    dob: date | None = None
    gender: Gender = Gender.UNKNOWN
    phone: str | None = None


class ClinicalRecord(BaseModel):
    """A single cleaned clinical record."""

    record_id: str
    patient_key: str
    record_type: RecordType
    record_date: date
    encounter_id: str | None = None
    author: str | None = None
    content: str
    source_file: str
    extra: dict[str, Any] = Field(default_factory=dict)


class RawRecord(BaseModel):
    """Untyped incoming record. All fields optional/tolerant; extras preserved."""

    model_config = {"extra": "allow"}

    record_id: str | None = None
    mrn: str | None = None
    patient_name: str | None = None
    name: str | None = None
    dob: str | date | None = None
    gender: str | None = None
    sex: str | None = None
    phone: str | None = None
    record_type: str | None = None
    type: str | None = None
    record_date: str | date | None = None
    date: str | date | None = None
    encounter_id: str | None = None
    encounter: str | None = None
    author: str | None = None
    provider: str | None = None
    content: str | None = None
    note: str | None = None
    text: str | None = None
    clinical_note: str | None = None


class CleanBatch(BaseModel):
    """Validated output of the ingestion pipeline."""

    patients: dict[str, PatientDemographics]
    records: list[ClinicalRecord]
    audit_log: list[AuditEntry]
    dropped_records: list[dict[str, Any]] = Field(default_factory=list)
