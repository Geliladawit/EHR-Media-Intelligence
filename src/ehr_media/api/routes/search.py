"""POST /search — semantic search across patient records."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from ehr_media import config
from ehr_media.api.schemas import SearchRequest, SearchResponse, SearchResult
from ehr_media.search.vectorstore import VectorStore

router = APIRouter()


def _store() -> VectorStore:
    return VectorStore(config.DB_PATH, config.DATA_DIR / "search" / "index.faiss")


@router.post("/search", response_model=SearchResponse)
def search_records(req: SearchRequest) -> SearchResponse:
    store = _store()
    try:
        start = time.perf_counter()
        hits = store.search(
            req.query,
            top_k=req.top_k,
            resource_type=req.resource_type,
            date_from=req.date_from,
            date_to=req.date_to,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
    finally:
        store.close()

    results = [SearchResult(**h) for h in hits]
    return SearchResponse(query=req.query, count=len(results), results=results)
