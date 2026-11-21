"""Full pipeline: ingest -> clean -> FHIR bundles -> summaries -> vector index.

Usage: python scripts/run_pipeline.py [--force-summaries] [--rebuild-index]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ehr_media import config
from ehr_media.fhir.bundles import build_all_bundles
from ehr_media.fhir.store import BundleStore, write_validation_report
from ehr_media.ingestion.pipeline import ingest_files, write_clean_output
from ehr_media.search.indexer import build_index
from ehr_media.summarization.cache import SummaryCache
from ehr_media.summarization.service import iter_bundles, summarize_patient
from ehr_media.summarization.summarizer import get_summarizer

SAMPLES = sorted(p for p in (config.DATA_DIR / "samples").glob("*")
                 if p.suffix in (".json", ".csv", ".txt", ".tsv"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-summaries", action="store_true")
    parser.add_argument("--rebuild-index", action="store_true")
    args = parser.parse_args()

    # 1. Ingest & clean
    print("== Ingestion ==")
    batch = ingest_files(SAMPLES)
    paths = write_clean_output(batch, config.DATA_DIR / "clean")
    print(f"  {len(batch.patients)} patients, {len(batch.records)} records, "
          f"{len(batch.audit_log)} audit entries, {len(batch.dropped_records)} dropped")

    # 2. FHIR bundles
    print("== FHIR R4 ==")
    bundles, errors = build_all_bundles(batch)
    store = BundleStore(config.DB_PATH)
    for key, bundle in bundles.items():
        store.upsert_bundle(key, bundle)
    store.close()
    report = write_validation_report(errors, config.DATA_DIR / "fhir" / "validation_report.json")
    print(f"  {len(bundles)} bundles, {sum(len(b['entry']) for b in bundles.values())} resources, "
          f"{len(errors)} validation errors -> {report}")

    # 3. Summaries
    print("== Summaries ==")
    summarizer = get_summarizer()
    cache = SummaryCache(config.DB_PATH)
    gen = cached = 0
    for key, bundle in iter_bundles(config.DB_PATH):
        _, was_cached = summarize_patient(bundle, key, summarizer, cache,
                                          force=args.force_summaries)
        gen += not was_cached
        cached += was_cached
    cache.close()
    print(f"  provider={summarizer.name} generated={gen} cached={cached}")

    # 4. Vector index
    print("== Search index ==")
    stats = build_index(config.DB_PATH, config.DATA_DIR / "search" / "index.faiss",
                        rebuild=args.rebuild_index)
    print(f"  {stats}")

    print("\nDone. Serve with: uvicorn ehr_media.api.main:app --port 8000")


if __name__ == "__main__":
    main()
