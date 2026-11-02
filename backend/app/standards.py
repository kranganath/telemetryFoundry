"""Pluggable standards registry loaded from JSON files.

Active registry: ``backend/data/standards/*.json``. New standards arrive via
the hydration pipeline (see ``hydrator``): MCP fetch -> ``pending`` review ->
``activate`` into this directory. Add a file by hand with this shape:

.. code-block:: json

    {
      "id": "my-standard",
      "name": "My Standard 1.0",
      "category": "data",
      "version": "1.0",
      "description": "...",
      "entities": [
        {"id": "record", "name": "Record",
         "fields": [{"path": "id", "type": "string", "required": true,
                     "description": "..."}]}
      ]
    }
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .models import StandardDefinition

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "standards"


@lru_cache(maxsize=1)
def load_standards() -> list[dict]:
    standards = []
    if not DATA_DIR.exists():
        return standards
    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            standards.append(StandardDefinition(**raw).model_dump())
        except Exception:
            continue
    return standards


def refresh() -> None:
    """Drop the registry cache (call after hydration/activation)."""
    load_standards.cache_clear()


def fingerprint() -> str:
    """Content hash of the active registry (drift detection baseline)."""
    import hashlib

    snapshot = {
        s.get("id"): {
            e.get("id"): [(f.get("path"), f.get("type"), f.get("required"))
                          for f in e.get("fields", [])]
            for e in s.get("entities", [])
        }
        for s in load_standards()
    }
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def list_standards() -> list[dict]:
    return [
        {
            "id": s.get("id"),
            "name": s.get("name"),
            "category": s.get("category"),
            "version": s.get("version"),
            "description": s.get("description"),
            "entities": [
                {"id": e.get("id"), "name": e.get("name"),
                 "field_count": len(e.get("fields", []))}
                for e in s.get("entities", [])
            ],
        }
        for s in load_standards()
    ]


def get_standard(standard_id: str) -> Optional[dict]:
    for s in load_standards():
        if s.get("id") == standard_id:
            return s
    return None


def get_entity(standard_id: str, entity_id: Optional[str]) -> tuple[Optional[dict], Optional[dict]]:
    standard = get_standard(standard_id)
    if not standard:
        return None, None
    entities = standard.get("entities", [])
    if not entities:
        return standard, None
    if entity_id is None:
        return standard, entities[0]
    for e in entities:
        if e.get("id") == entity_id:
            return standard, e
    return standard, None
