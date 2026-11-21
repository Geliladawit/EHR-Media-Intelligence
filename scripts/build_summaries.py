"""Generate AI summaries for every patient bundle and cache them.

Usage:
  python scripts/build_summaries.py            # cached patients are skipped
  python scripts/build_summaries.py --force    # regenerate everything

Provider is chosen via .env (EHR_LLM_PROVIDER + keys); falls back to mock.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ehr_media import config
from ehr_media.summarization.cache import SummaryCache
from ehr_media.summarization.service import iter_bundles, summarize_patient
from ehr_media.summarization.summarizer import get_summarizer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="ignore cache and regenerate all summaries")
    parser.add_argument("--delay", type=float, default=4.0,
                        help="seconds between LLM calls (free-tier rate limits)")
    parser.add_argument("--db", default=str(config.DB_PATH))
    args = parser.parse_args()

    summarizer = get_summarizer()
    cache = SummaryCache(args.db)

    total = cached = regenerated = 0
    for patient_key, bundle in iter_bundles(args.db):
        total += 1
        summary, was_cached = summarize_patient(
            bundle, patient_key, summarizer, cache, force=args.force)
        if was_cached:
            cached += 1
        else:
            regenerated += 1
            wc = len(summary.summary_text.split())
            print(f"  {patient_key}: generated ({wc} words, model={summary.model})")
            time.sleep(args.delay)  # stay under free-tier req/min limits

    cache.close()
    print(f"\nProvider : {summarizer.name}")
    print(f"Patients : {total} | cached: {cached} | generated: {regenerated}")
    print(f"Cache    : {args.db} (summaries table)")


if __name__ == "__main__":
    main()
