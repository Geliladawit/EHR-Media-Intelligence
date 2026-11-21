"""Start the API + UI dev server.

Usage: python scripts/serve.py [--port 8000]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn
    uvicorn.run("ehr_media.api.main:app", host="127.0.0.1",
                port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
