"""Input readers for heterogeneous EHR export formats.

Supported inputs:
  - JSON export: a list of record objects, or {"records": [...]} / {"patients": [...]}
  - CSV / plain-text notes export: header row + one record per line
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from .models import RawRecord


def _coerce_records(items: Iterable[dict[str, Any]]) -> list[RawRecord]:
    return [RawRecord.model_validate(item) for item in items]


def read_json(path: str | Path) -> list[RawRecord]:
    """Read a simulated EHR JSON export into tolerant RawRecords."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    if isinstance(data, dict):
        for key in ("records", "patients", "data", "results"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of records")

    return _coerce_records(data)


def read_csv(path: str | Path) -> list[RawRecord]:
    """Read a CSV export (scanned notes / lab results) into RawRecords.

    Accepts common header aliases via RawRecord field aliases handled in cleaning.
    """
    records: list[RawRecord] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            cleaned_row = {k.strip().lower(): (v.strip() if isinstance(v, str) else v)
                           for k, v in row.items() if k is not None}
            records.append(RawRecord.model_validate(cleaned_row))
    return records


def read_plain_text(path: str | Path) -> list[RawRecord]:
    """Read a delimited plain-text notes file (pipe or tab separated)."""
    records: list[RawRecord] = []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        return records

    header = [h.strip().lower() for h in (lines[0].split("|") if "|" in lines[0] else lines[0].split("\t"))]
    delim = "|" if "|" in lines[0] else "\t"
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(delim)]
        row = dict(zip(header, parts))
        records.append(RawRecord.model_validate(row))
    return records


def read_export(path: str | Path) -> list[RawRecord]:
    """Dispatch on file extension."""
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return read_json(path)
    if suffix == ".csv":
        return read_csv(path)
    if suffix in (".txt", ".tsv"):
        return read_plain_text(path)
    raise ValueError(f"Unsupported export format: {path}")
