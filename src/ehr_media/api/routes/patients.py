"""Patient-facing endpoints for the UI detail drawer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ehr_media import config
from ehr_media.api.schemas import PatientResource, PatientSummary
from ehr_media.summarization.service import decode_document_text, iter_bundles

router = APIRouter()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_bundle(patient_key: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT bundle_json FROM bundles WHERE patient_key=?",
            (patient_key,)).fetchone()
    finally:
        conn.close()
    return json.loads(row["bundle_json"]) if row else None


@router.get("/patients")
def list_patients() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT patient_key, patient_name, mrn, MIN(record_date) AS first_date,"
            " MAX(record_date) AS last_date, COUNT(*) AS record_count"
            " FROM embeddings_meta GROUP BY patient_key"
            " ORDER BY patient_name").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@router.get("/patients/{patient_key}", response_model=PatientSummary)
def get_patient_summary(patient_key: str) -> PatientSummary:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT summary_json FROM summaries WHERE patient_key=?",
            (patient_key,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "No summary for patient")
    data = json.loads(row["summary_json"])
    return PatientSummary(**data)


@router.get("/patients/{patient_key}/resources", response_model=list[PatientResource])
def get_patient_resources(patient_key: str) -> list[PatientResource]:
    bundle = _get_bundle(patient_key)
    if bundle is None:
        raise HTTPException(404, "Patient not found")

    resources: list[PatientResource] = []
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        rtype = res.get("resourceType")
        if rtype == "Patient":
            continue
        if rtype == "DocumentReference":
            resources.append(PatientResource(
                resource_type=rtype,
                record_id=res.get("content", [{}])[0].get("attachment", {}).get("title"),
                record_date=(res.get("date") or "")[:10],
                resource_type_label=res.get("type", {}).get("text"),
                content=decode_document_text(entry),
                author=(res.get("author") or [{}])[0].get("display"),
            ))
        elif rtype == "DiagnosticReport":
            resources.append(PatientResource(
                resource_type=rtype,
                record_id=res.get("id"),
                record_date=(res.get("effectiveDateTime") or "")[:10],
                resource_type_label=res.get("code", {}).get("text"),
                content=res.get("conclusion"),
                author=(res.get("performer") or [{}])[0].get("display"),
            ))
        elif rtype == "Encounter":
            resources.append(PatientResource(
                resource_type=rtype,
                record_id=res.get("id"),
                record_date=(res.get("period", {}).get("start") or "")[:10],
                resource_type_label="encounter",
                content=None,
            ))
    resources.sort(key=lambda r: r.record_date or "", reverse=True)
    return resources
