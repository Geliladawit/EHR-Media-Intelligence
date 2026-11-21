"""Build/rebuild the FAISS vector index from FHIR bundles in SQLite.

Usage:
  python scripts/build_index.py           # incremental (skips existing records)
  python scripts/build_index.py --rebuild # drop metadata + re-embed everything
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ehr_media import config
from ehr_media.search.indexer import build_index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    stats = build_index(config.DB_PATH,
                        config.DATA_DIR / "search" / "index.faiss",
                        rebuild=args.rebuild)
    print(stats)


if __name__ == "__main__":
    main()
