"""FAISS vector store with SQLite metadata join.

Vectors live in faiss.IndexIDMap(IndexFlatIP(384)) where FAISS ids are the
exact embeddings_meta.id primary keys — a deterministic join that survives
re-indexing. Metadata (patient, resource_type, date, snippet) stays in SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from ehr_media.search.embeddings import EMBEDDING_DIM, embed_query, embed_texts

SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_key TEXT NOT NULL,
    record_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    record_date TEXT NOT NULL,
    patient_name TEXT,
    mrn TEXT,
    text TEXT NOT NULL,
    snippet TEXT NOT NULL,
    UNIQUE(patient_key, record_id)
);
CREATE INDEX IF NOT EXISTS idx_meta_patient ON embeddings_meta(patient_key);
CREATE INDEX IF NOT EXISTS idx_meta_type_date ON embeddings_meta(resource_type, record_date);
"""

SNIPPET_CHARS = 240


class VectorStore:
    def __init__(self, db_path: str | Path, index_path: str | Path):
        self.db_path = Path(db_path)
        self.index_path = Path(index_path)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

        if self.index_path.exists():
            self.index = faiss.read_index(str(self.index_path))
        else:
            self.index = faiss.IndexIDMap(faiss.IndexFlatIP(EMBEDDING_DIM))

    # -- indexing ---------------------------------------------------------

    def existing_keys(self) -> set[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT patient_key, record_id FROM embeddings_meta").fetchall()
        return set(rows)

    def add_documents(self, docs: list[dict]) -> int:
        """Insert new documents (skipping existing patient_key+record_id).

        Each doc: patient_key, record_id, resource_type, record_date,
        patient_name, mrn, text. Returns count added.
        """
        existing = self.existing_keys()
        new_docs = [d for d in docs
                    if (d["patient_key"], d["record_id"]) not in existing]
        if not new_docs:
            return 0

        texts = [d["text"] for d in new_docs]
        vectors = embed_texts(texts)

        ids = []
        for d in new_docs:
            cur = self._conn.execute(
                "INSERT INTO embeddings_meta"
                " (patient_key, record_id, resource_type, record_date,"
                "  patient_name, mrn, text, snippet)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (d["patient_key"], d["record_id"], d["resource_type"],
                 d["record_date"], d.get("patient_name"), d.get("mrn"),
                 d["text"], d["text"][:SNIPPET_CHARS]))
            ids.append(cur.lastrowid)
        self._conn.commit()

        self.index.add_with_ids(vectors, np.asarray(ids, dtype=np.int64))
        self.save_index()
        return len(new_docs)

    def rebuild(self) -> int:
        """Drop and re-embed everything currently in embeddings_meta."""
        self.index = faiss.IndexIDMap(faiss.IndexFlatIP(EMBEDDING_DIM))
        rows = self._conn.execute(
            "SELECT id, text FROM embeddings_meta ORDER BY id").fetchall()
        if not rows:
            self.save_index()
            return 0
        ids = np.asarray([r[0] for r in rows], dtype=np.int64)
        vectors = embed_texts([r[1] for r in rows])
        self.index.add_with_ids(vectors, ids)
        self.save_index()
        return len(rows)

    def save_index(self) -> None:
        faiss.write_index(self.index, str(self.index_path))

    # -- querying ---------------------------------------------------------

    def _filter_ids(self, resource_type: str | None,
                    date_from: str | None, date_to: str | None) -> list[int] | None:
        """SQL prefilter -> candidate FAISS ids, or None if unfiltered."""
        if not (resource_type or date_from or date_to):
            return None
        sql = "SELECT id FROM embeddings_meta WHERE 1=1"
        params: list[Any] = []
        if resource_type:
            sql += " AND resource_type = ?"
            params.append(resource_type)
        if date_from:
            sql += " AND record_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND record_date <= ?"
            params.append(date_to)
        rows = self._conn.execute(sql, params).fetchall()
        return [r[0] for r in rows]

    def search(self, query: str, top_k: int = 5,
               resource_type: str | None = None,
               date_from: str | None = None,
               date_to: str | None = None) -> list[dict]:
        """Semantic search with metadata filters.

        With filters active, FAISS is queried through an IDSelectorBatch built
        from the matching embeddings_meta ids — exact filtering, no under-fill.
        Scores are inner products on L2-normalized vectors, clamped to [0,1].
        """
        if self.index.ntotal == 0:
            return []

        qvec = embed_query(query).astype(np.float32)
        allowed = self._filter_ids(resource_type, date_from, date_to)

        if allowed is not None:
            if not allowed:
                return []
            sel = faiss.IDSelectorBatch(np.asarray(allowed, dtype=np.int64))
            params = faiss.SearchParameters(sel=sel)
            k = min(len(allowed), top_k)
            scores, ids = self.index.search(qvec, k, params=params)
        else:
            k = min(self.index.ntotal, top_k)
            scores, ids = self.index.search(qvec, k)

        results = []
        for score, fid in zip(scores[0], ids[0]):
            if fid == -1:
                continue
            meta = self._conn.execute(
                "SELECT patient_key, record_id, resource_type, record_date,"
                " patient_name, mrn, snippet FROM embeddings_meta WHERE id=?",
                (int(fid),)).fetchone()
            if meta is None:
                continue
            results.append({
                "patient_key": meta[0],
                "record_id": meta[1],
                "resource_type": meta[2],
                "record_date": meta[3],
                "patient_name": meta[4],
                "mrn": meta[5],
                "snippet": meta[6],
                "score": max(0.0, min(1.0, float(score))),
            })
        return results

    def stats(self) -> dict:
        n = self.index.ntotal
        meta = self._conn.execute("SELECT COUNT(*) FROM embeddings_meta").fetchone()[0]
        return {"index_vectors": n, "meta_rows": meta}

    def close(self) -> None:
        self._conn.close()
