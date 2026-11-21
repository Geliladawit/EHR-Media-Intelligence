"""Summarization service: FHIR Bundle -> patient context -> summary.

Extracts clinical text from DocumentReference attachments, builds a stable
record_hash for cache invalidation, and orchestrates summarizer + cache.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from ehr_media.summarization.cache import SummaryCache
from ehr_media.summarization.summarizer import (
    ClinicalSummary,
    Summarizer,
    get_summarizer,
)

MAX_CONTEXT_CHARS = 8000


def decode_document_text(entry: dict) -> str | None:
    """Pull decoded text out of a DocumentReference bundle entry."""
    res = entry.get("resource", {})
    if res.get("resourceType") != "DocumentReference":
        return None
    for content in res.get("content", []):
        data = content.get("attachment", {}).get("data")
        if data:
            return base64.b64decode(data).decode("utf-8", errors="replace")
    return None


def extract_patient_context(bundle: dict) -> tuple[str, str]:
    """Build (patient_header, records_block, record_hash) from a Bundle.

    Returns (header, records_text, record_hash). Records sorted newest-first.
    """
    patient = next((e["resource"] for e in bundle.get("entry", [])
                    if e["resource"].get("resourceType") == "Patient"), {})

    name = " ".join(patient.get("name", [{}])[0].get("given", [])
                    + [patient.get("name", [{}])[0].get("family", "")]).strip() \
        if patient.get("name") else "Unknown"
    header = (f"Patient: {name or 'Unknown'} | "
              f"Gender: {patient.get('gender', 'unknown')} | "
              f"DOB: {patient.get('birthDate', 'unknown')}")

    records: list[tuple[str, str, str]] = []
    hash_parts: list[str] = []
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        if res.get("resourceType") != "DocumentReference":
            continue
        text = decode_document_text(entry)
        if text is None:
            continue
        rtype = res.get("type", {}).get("text", "record")
        rdate = res.get("date", "")[:10]
        title = res.get("content", [{}])[0].get("attachment", {}).get("title", "")
        records.append((rdate, rtype, text))
        hash_parts.append(f"{title}:{hashlib.sha256(text.encode()).hexdigest()[:16]}")

    records.sort(key=lambda t: t[0], reverse=True)
    lines = [f"[{d}] {t}: {txt}" for d, t, txt in records]
    records_text = "\n\n".join(lines)[:MAX_CONTEXT_CHARS]

    record_hash = hashlib.sha256(
        "|".join(sorted(hash_parts)).encode()).hexdigest()
    return header, records_text, record_hash


def summarize_patient(
    bundle: dict,
    patient_key: str,
    summarizer: Summarizer,
    cache: SummaryCache,
    force: bool = False,
) -> tuple[ClinicalSummary, bool]:
    """Return (summary, was_cached). Uses cache unless hash changed or force."""
    header, records_text, record_hash = extract_patient_context(bundle)

    if not force:
        cached = cache.get(patient_key, record_hash)
        if cached:
            return ClinicalSummary.model_validate(cached), True

    context = f"{header}\n\n{records_text}"
    result = summarizer.summarize(context)
    summary = ClinicalSummary(
        patient_key=patient_key,
        record_hash=record_hash,
        model=summarizer.name,
        **result,
    )
    cache.put(summary, record_hash)
    return summary, False


def iter_bundles(db_path: str | Path):
    """Yield (patient_key, bundle_dict) from the bundles table.

    Materializes all rows before returning so the read connection is closed
    before callers write summaries back to the same database file.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT patient_key, bundle_json FROM bundles").fetchall()
    finally:
        conn.close()
    for key, blob in rows:
        yield key, json.loads(blob)
