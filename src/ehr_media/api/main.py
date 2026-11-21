"""FastAPI application factory, routers, static mount, auto-bootstrap."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ehr_media import config
from ehr_media.api.routes import patients, search

log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
INDEX_PATH = config.DATA_DIR / "search" / "index.faiss"


def _db_ready() -> bool:
    if not config.DB_PATH.exists():
        return False
    try:
        conn = sqlite3.connect(config.DB_PATH)
        n = conn.execute("SELECT COUNT(*) FROM bundles").fetchone()[0]
        conn.close()
        return n > 0
    except sqlite3.OperationalError:
        return False


def _ensure_initialized() -> None:
    """Bootstrap the full pipeline if the DB/index don't exist yet.

    Runs: ingest samples -> FHIR bundles -> summaries (configured provider,
    mock fallback) -> vector index. Makes a fresh clone work with one command.
    """
    need_db = not _db_ready()
    need_index = not INDEX_PATH.exists()
    if not need_db and not need_index:
        return

    from ehr_media.fhir.bundles import build_all_bundles
    from ehr_media.fhir.store import BundleStore
    from ehr_media.ingestion.pipeline import ingest_files, write_clean_output
    from ehr_media.search.indexer import build_index
    from ehr_media.summarization.cache import SummaryCache
    from ehr_media.summarization.service import iter_bundles, summarize_patient
    from ehr_media.summarization.summarizer import get_summarizer

    if need_db:
        log.info("Database missing/empty — running full pipeline bootstrap")
        samples = sorted((config.DATA_DIR / "samples").glob("*"))
        batch = ingest_files([p for p in samples if p.suffix in (".json", ".csv", ".txt", ".tsv")])
        write_clean_output(batch, config.DATA_DIR / "clean")

        bundles, errors = build_all_bundles(batch)
        store = BundleStore(config.DB_PATH)
        for key, bundle in bundles.items():
            store.upsert_bundle(key, bundle)
        store.close()
        log.info("Bootstrapped %d bundles (%d validation errors)",
                 len(bundles), len(errors))

        # Summaries with whatever provider is configured (mock if no key)
        summarizer = get_summarizer()
        cache = SummaryCache(config.DB_PATH)
        for key, bundle in iter_bundles(config.DB_PATH):
            summarize_patient(bundle, key, summarizer, cache)
        cache.close()
        log.info("Summaries generated with provider: %s", summarizer.name)

    if need_index:
        log.info("Building vector index…")
        stats = build_index(config.DB_PATH, INDEX_PATH)
        log.info("Index built: %s", stats)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_initialized()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="EHR Media Intelligence", version="0.1.0",
                  lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(search.router)
    app.include_router(patients.router)

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True),
                  name="frontend")
    return app


app = create_app()
