"""Build search documents from FHIR bundles and index them.

Embedding text is raw record text + record metadata ONLY:
    "{record_type} | {date} | {decoded clinical text}"
The patient-level AI summary is deliberately excluded — it lives behind
GET /patients/{key} so record-level filters stay precise.
"""

from __future__ import annotations

import base64
from pathlib import Path

from ehr_media.summarization.service import decode_document_text, iter_bundles
from ehr_media.search.vectorstore import VectorStore


def _patient_info(bundle: dict) -> tuple[str | None, str | None]:
    """Extract (display_name, mrn) from the bundle's Patient resource."""
    patient = next((e["resource"] for e in bundle.get("entry", [])
                    if e["resource"].get("resourceType") == "Patient"), {})
    name = ""
    for n in patient.get("name", []):
        given = " ".join(n.get("given", []))
        name = f"{given} {n.get('family', '')}".strip() or name
    mrn = next((i.get("value") for i in patient.get("identifier", [])
                if i.get("system", "").endswith("/mrn")), None)
    return (name or None), mrn


def extract_documents(bundle: dict, patient_key: str) -> list[dict]:
    """One search document per DocumentReference in the bundle."""
    patient_name, mrn = _patient_info(bundle)
    docs = []
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        if res.get("resourceType") != "DocumentReference":
            continue
        text = decode_document_text(entry)
        if not text:
            continue
        rtype = res.get("type", {}).get("text", "record")
        rdate = (res.get("date") or "")[:10]
        record_id = res.get("content", [{}])[0].get("attachment", {}).get("title", "")
        docs.append({
            "patient_key": patient_key,
            "record_id": record_id or res.get("id", "unknown"),
            "resource_type": rtype,
            "record_date": rdate,
            "patient_name": patient_name,
            "mrn": mrn,
            "text": f"{rtype} | {rdate} | {text}",
        })
    return docs


def build_index(db_path: str | Path, index_path: str | Path,
                rebuild: bool = False) -> dict:
    """Index all bundles. Returns stats dict."""
    store = VectorStore(db_path, index_path)
    if rebuild:
        # clear metadata so everything re-embeds
        store._conn.execute("DELETE FROM embeddings_meta")
        store._conn.commit()

    total_docs = added = 0
    for patient_key, bundle in iter_bundles(db_path):
        docs = extract_documents(bundle, patient_key)
        total_docs += len(docs)
        added += store.add_documents(docs)

    stats = store.stats()
    stats.update({"documents_seen": total_docs, "documents_added": added})
    store.close()
    return stats
