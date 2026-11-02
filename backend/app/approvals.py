"""Human-approval store. Export requires an approved mapping set."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "data" / "approvals.json"


def _read() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write(records: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(records, indent=2), encoding="utf-8")


def create_approval(payload: dict) -> dict:
    records = _read()
    approval_id = f"APR-{uuid.uuid4().hex[:8].upper()}"
    record = {
        "id": approval_id,
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    records[approval_id] = record
    _write(records)
    return record


def get_approval(approval_id: str) -> Optional[dict]:
    return _read().get(approval_id)


def list_approvals() -> list[dict]:
    records = sorted(_read().values(),
                     key=lambda r: r.get("approved_at", ""), reverse=True)
    return [
        {
            "id": r.get("id"),
            "status": r.get("status"),
            "standard_id": r.get("standard_id"),
            "entity_id": r.get("entity_id"),
            "approved_by": r.get("approved_by"),
            "approved_at": r.get("approved_at"),
            "mapping_count": len(r.get("mappings", [])),
            "valid": (r.get("validation") or {}).get("valid"),
        }
        for r in records
    ]


from typing import Optional  # noqa: E402
