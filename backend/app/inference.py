"""Infer a source schema from an uploaded CSV/JSON log sample."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
MAX_SCAN_ROWS = 2000
SAMPLE_ROWS = 20

_BOOL = {"true": True, "false": False}
_DATE_FMTS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y")
_TS_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d/%b/%Y:%H:%M:%S %z")


def parse_rows(filename: str, content: bytes) -> list[dict[str, Any]]:
    name = (filename or "").lower()
    if name.endswith(".csv") or name.endswith(".tsv"):
        return parse_csv(content, "\t" if name.endswith(".tsv") else ",")
    if name.endswith((".jsonl", ".ndjson", ".log")):
        return parse_json_lines(content)
    if name.endswith(".json"):
        return parse_json(content)
    # Sniff unknown extensions.
    text = content.lstrip()[:1]
    if text in (b"[", b"{"):
        try:
            return parse_json(content)
        except ValueError:
            return parse_json_lines(content)
    return parse_csv(content, ",")


def parse_csv(content: bytes, delimiter: str) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        return [dict(r) for r in reader if any((v or "").strip() for v in r.values())]
    except Exception as exc:
        raise ValueError(f"Cannot parse CSV: {exc}") from exc


def parse_json(content: bytes) -> list[dict[str, Any]]:
    try:
        doc = json.loads(content.decode("utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Cannot parse JSON: {exc}") from exc
    if isinstance(doc, dict):
        for key in ("rows", "records", "events", "data", "logs"):
            if isinstance(doc.get(key), list):
                doc = doc[key]
                break
        else:
            doc = [doc]
    if not isinstance(doc, list) or not all(isinstance(r, dict) for r in doc):
        raise ValueError("JSON must be an object, or an array of objects")
    return doc


def parse_json_lines(content: bytes) -> list[dict[str, Any]]:
    rows = []
    for i, line in enumerate(content.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception as exc:
            raise ValueError(f"Cannot parse JSON on line {i}: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"Line {i} is not a JSON object")
        rows.append(obj)
    return rows


def _classify(values: list[str]) -> str:
    non_empty = [v for v in values if v != "" and v is not None]
    if not non_empty:
        return "string"
    if all(v.strip().lower() in _BOOL for v in non_empty):
        return "boolean"
    try:
        for v in non_empty:
            int(str(v).strip())
        return "integer"
    except ValueError:
        pass
    try:
        for v in non_empty:
            float(str(v).strip())
        return "decimal"
    except ValueError:
        pass
    if all(_is_date(v) for v in non_empty):
        return "datetime" if any(" " in str(v) or "T" in str(v) for v in non_empty) else "date"
    return "string"


def _is_date(value: Any) -> bool:
    text = str(value).strip()
    for fmt in _DATE_FMTS + _TS_FMTS:
        try:
            datetime.strptime(text.split(".")[0], fmt)
            return True
        except ValueError:
            continue
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def infer_schema(filename: str, content: bytes) -> dict:
    """Return {schema, sample_rows, row_count, truncated} for an upload."""
    if len(content) > MAX_BYTES:
        raise ValueError(f"File exceeds {MAX_BYTES // 1024 // 1024}MB limit")
    try:
        rows = parse_rows(filename, content)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Cannot parse file: {exc}") from exc
    if not rows:
        raise ValueError("No data rows found")
    scanned = rows[:MAX_SCAN_ROWS]
    columns: list[str] = []
    for row in scanned:
        for key in row:
            if key not in columns:
                columns.append(key)
    fields = []
    for col in columns:
        values = ["" if r.get(col) is None else str(r.get(col)) for r in scanned]
        sample = next((v for v in values if v != ""), "")
        fields.append({"name": col, "type": _classify(values), "sample": sample})
    stem = (filename or "upload").rsplit("/", 1)[-1].rsplit(".", 1)[0] or "upload"
    return {
        "schema": {"name": stem.replace("-", "_").replace(" ", "_"), "fields": fields},
        "sample_rows": [{c: (r.get(c, "")) for c in columns} for r in rows[:SAMPLE_ROWS]],
        "row_count": len(rows),
        "truncated": len(rows) > MAX_SCAN_ROWS,
    }
