"""Cleaning & normalization rules for raw EHR records.

Every transformation emits an AuditEntry so each output record carries a
per-record history of what changed and why.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any

from .models import (
    AuditEntry,
    CleanBatch,
    ClinicalRecord,
    Gender,
    PatientDemographics,
    RawRecord,
    RecordType,
)

# ---------------------------------------------------------------------------
# Field normalizers
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%Y%m%d",
]

_GENDER_MAP = {
    "m": Gender.MALE, "male": Gender.MALE, "man": Gender.MALE, "1": Gender.MALE,
    "f": Gender.FEMALE, "female": Gender.FEMALE, "woman": Gender.FEMALE, "2": Gender.FEMALE,
    "u": Gender.UNKNOWN, "unknown": Gender.UNKNOWN, "other": Gender.UNKNOWN,
    "o": Gender.UNKNOWN, "": Gender.UNKNOWN, "n/a": Gender.UNKNOWN, "na": Gender.UNKNOWN,
}

_RECORD_TYPE_MAP = {
    "lab": RecordType.LAB, "lab_result": RecordType.LAB, "laboratory": RecordType.LAB,
    "labs": RecordType.LAB, "bloodwork": RecordType.LAB,
    "imaging": RecordType.IMAGING, "xray": RecordType.IMAGING, "x-ray": RecordType.IMAGING,
    "ct": RecordType.IMAGING, "ct_scan": RecordType.IMAGING, "mri": RecordType.IMAGING,
    "radiology": RecordType.IMAGING, "ultrasound": RecordType.IMAGING,
    "discharge_summary": RecordType.DISCHARGE_SUMMARY, "discharge": RecordType.DISCHARGE_SUMMARY,
    "dc_summary": RecordType.DISCHARGE_SUMMARY,
    "clinical_note": RecordType.CLINICAL_NOTE, "note": RecordType.CLINICAL_NOTE,
    "progress_note": RecordType.CLINICAL_NOTE, "consult": RecordType.CLINICAL_NOTE,
    "consultation": RecordType.CLINICAL_NOTE, "h_and_p": RecordType.CLINICAL_NOTE,
}


def normalize_mrn(value: str | None) -> str | None:
    """Canonical MRN: uppercase alphanumerics only."""
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    return cleaned or None


def normalize_name(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value)).strip().title() or None


def normalize_date(value: Any) -> date | None:
    """Parse the many date formats found in EHR exports into ISO date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def normalize_gender(value: str | None) -> Gender:
    if value is None:
        return Gender.UNKNOWN
    return _GENDER_MAP.get(str(value).strip().lower(), Gender.UNKNOWN)


def normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or None


def normalize_record_type(value: str | None) -> RecordType | None:
    if value is None:
        return None
    return _RECORD_TYPE_MAP.get(str(value).strip().lower())


def _pick(raw: RawRecord, *names: str) -> Any:
    """First non-empty value among candidate field names."""
    for name in names:
        value = getattr(raw, name, None)
        if value is not None and str(value).strip() != "":
            return value
    # fall back to extras for unrecognized header variants
    extras = raw.model_dump(exclude_none=True)
    for name in names:
        if name in extras and str(extras[name]).strip() != "":
            return extras[name]
    return None


def _first_content(raw: RawRecord) -> str | None:
    for name in ("content", "note", "text", "clinical_note"):
        value = _pick(raw, name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class _PatientResolver:
    """Assigns a stable patient_key and merges demographics across records."""

    def __init__(self) -> None:
        self.patients: dict[str, PatientDemographics] = {}
        self._mrn_to_key: dict[str, str] = {}
        self._conflicts: list[AuditEntry] = []

    def resolve(self, raw: RawRecord, audit: list[AuditEntry], rid: str) -> str | None:
        mrn_raw = _pick(raw, "mrn")
        name_raw = _pick(raw, "patient_name", "name")
        dob_raw = _pick(raw, "dob")
        gender_raw = _pick(raw, "gender", "sex")
        phone_raw = _pick(raw, "phone")

        mrn = normalize_mrn(str(mrn_raw) if mrn_raw is not None else None)
        name = normalize_name(str(name_raw) if name_raw is not None else None)
        dob = normalize_date(dob_raw)
        gender = normalize_gender(str(gender_raw) if gender_raw is not None else None)
        phone = normalize_phone(str(phone_raw) if phone_raw is not None else None)

        # Track which normalizations happened
        if mrn_raw is not None and str(mrn_raw) != mrn:
            audit.append(AuditEntry(record_id=rid, field="mrn", rule="normalize_mrn",
                                    old_value=mrn_raw, new_value=mrn))
        if name_raw is not None and str(name_raw) != name:
            audit.append(AuditEntry(record_id=rid, field="patient_name", rule="normalize_name",
                                    old_value=name_raw, new_value=name))
        if dob_raw is not None and normalize_date(dob_raw) is not None and str(dob_raw) != dob.isoformat():
            audit.append(AuditEntry(record_id=rid, field="dob", rule="normalize_date",
                                    old_value=str(dob_raw), new_value=dob.isoformat()))
        if gender_raw is not None and normalize_gender(str(gender_raw)) != Gender.UNKNOWN \
                and str(gender_raw).strip().lower() not in ("m", "male", "f", "female"):
            audit.append(AuditEntry(record_id=rid, field="gender", rule="normalize_gender",
                                    old_value=gender_raw, new_value=gender.value))

        if not mrn and not name:
            return None

        if mrn:
            key = self._mrn_to_key.get(mrn)
            if key is None:
                key = f"MRN:{mrn}"
                self._mrn_to_key[mrn] = key
        else:
            key = f"NAME:{(name or 'UNKNOWN').upper()}|DOB:{dob.isoformat() if dob else 'UNKNOWN'}"

        patient = self.patients.get(key)
        if patient is None:
            patient = PatientDemographics(
                patient_key=key,
                mrn=mrn,
                full_name=name or "Unknown",
                dob=dob,
                gender=gender,
                phone=phone,
            )
            self.patients[key] = patient
            return key

        # Merge: fill gaps, detect conflicts
        conflicts = []
        if patient.dob is None and dob is not None:
            patient.dob = dob
        elif patient.dob is not None and dob is not None and patient.dob != dob:
            conflicts.append(("dob", patient.dob.isoformat(), dob.isoformat()))
        if patient.gender == Gender.UNKNOWN and gender != Gender.UNKNOWN:
            patient.gender = gender
        if patient.phone is None and phone is not None:
            patient.phone = phone
        if patient.full_name == "Unknown" and name:
            patient.full_name = name

        if conflicts:
            for field, old, new in conflicts:
                self._conflicts.append(AuditEntry(
                    record_id=rid, field=f"patient.{field}", rule="identifier_conflict",
                    old_value=old, new_value=new,
                ))
        return key


def _record_hash(patient_key: str, record_type: RecordType, rec_date: date, content: str) -> str:
    basis = f"{patient_key}|{record_type.value}|{rec_date.isoformat()}|{content.strip().lower()}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def clean_records(
    raw_records: list[RawRecord],
    source_file: str = "<memory>",
) -> CleanBatch:
    """Clean a list of RawRecords into validated ClinicalRecords + audit log."""
    resolver = _PatientResolver()
    audit: list[AuditEntry] = []
    records: list[ClinicalRecord] = []
    dropped: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    for index, raw in enumerate(raw_records):
        rid = str(_pick(raw, "record_id") or f"{source_file}:{index}")
        rec_audit: list[AuditEntry] = []

        record_type = normalize_record_type(_pick(raw, "record_type", "type"))
        rec_date = normalize_date(_pick(raw, "record_date", "date"))
        content = _first_content(raw)
        encounter = _pick(raw, "encounter_id", "encounter")
        author = _pick(raw, "author", "provider")

        if _pick(raw, "record_date", "date") is not None and rec_date is not None:
            raw_date = str(_pick(raw, "record_date", "date"))
            if raw_date != rec_date.isoformat():
                rec_audit.append(AuditEntry(record_id=rid, field="record_date",
                                            rule="normalize_date",
                                            old_value=raw_date, new_value=rec_date.isoformat()))
        if _pick(raw, "record_type", "type") is not None and record_type is not None:
            raw_type = str(_pick(raw, "record_type", "type"))
            if raw_type != record_type.value:
                rec_audit.append(AuditEntry(record_id=rid, field="record_type",
                                            rule="normalize_record_type",
                                            old_value=raw_type, new_value=record_type.value))

        # Resolve patient identity first — needed for dedup key
        patient_key = resolver.resolve(raw, rec_audit, rid)
        if patient_key is None:
            dropped.append({"record_id": rid, "reason": "missing patient identity"})
            audit.append(AuditEntry(record_id=rid, field="*", rule="dropped",
                                    old_value=None, new_value="missing patient identity"))
            continue

        # Drop records that can't be placed or carry no content
        if record_type is None or rec_date is None or content is None:
            reasons = []
            if record_type is None:
                reasons.append("unrecognized record_type")
            if rec_date is None:
                reasons.append("missing/invalid record_date")
            if content is None:
                reasons.append("missing content")
            reason = "; ".join(reasons)
            dropped.append({"record_id": rid, "reason": reason})
            audit.append(AuditEntry(record_id=rid, field="*", rule="dropped",
                                    old_value=None, new_value=reason))
            continue

        # Dedup on patient + type + date + content
        h = _record_hash(patient_key, record_type, rec_date, content)
        if h in seen_hashes:
            dropped.append({"record_id": rid, "reason": "duplicate record"})
            audit.append(AuditEntry(record_id=rid, field="*", rule="dedup",
                                    old_value=None, new_value="duplicate of earlier record"))
            continue
        seen_hashes.add(h)

        records.append(ClinicalRecord(
            record_id=rid,
            patient_key=patient_key,
            record_type=record_type,
            record_date=rec_date,
            encounter_id=str(encounter) if encounter not in (None, "") else None,
            author=str(author) if author not in (None, "") else None,
            content=content,
            source_file=source_file,
        ))
        audit.extend(rec_audit)

    audit.extend(resolver._conflicts)
    return CleanBatch(patients=resolver.patients, records=records,
                      audit_log=audit, dropped_records=dropped)
