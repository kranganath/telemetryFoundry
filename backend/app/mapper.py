"""Mapping suggestions: offline heuristic, optionally LLM-assisted."""

from __future__ import annotations

import re
from typing import Any

from .llm import chat_json, last_error, llm_enabled, model_name
from .models import MappingEntry, SourceSchema
from .validator import TYPE_GROUPS

# Cap targets sent to the LLM so large OCSF classes stay within budget.
LLM_TARGET_CAP = 120


def _tokens(text: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return set(t for t in re.split(r"[^a-z0-9]+", spaced.lower()) if t)


def _score(source: str, field: dict) -> tuple[float, str]:
    src = _tokens(source)
    path = field.get("path", "")
    name_tokens = _tokens(path.replace(".", " "))
    desc_tokens = _tokens(field.get("description", ""))
    hit = src & name_tokens
    score = 0.0
    if hit:
        score = len(hit) / max(len(src | name_tokens), 1) + 0.3 * len(hit)
    elif src & desc_tokens:
        score = 0.25
    # Leaf-name token overlap is a strong signal.
    leaf_tokens = _tokens(path.split(".")[-1])
    if leaf_tokens & src:
        score += 0.5
    return score, path


def suggest_offline(source: SourceSchema, entity: dict) -> list[MappingEntry]:
    fields = entity.get("fields", [])
    used: set[str] = set()
    suggestions: list[MappingEntry] = []
    for src in source.fields:
        ranked = sorted(
            ((_score(src.name, f), f) for f in fields if f.get("path") not in used),
            key=lambda item: item[0][0],
            reverse=True,
        )
        if ranked and ranked[0][0][0] > 0:
            (score, path), field = ranked[0]
            used.add(path)
            confidence = round(min(0.95, 0.45 + score), 2)
            transform = "direct"
            src_group = TYPE_GROUPS.get((src.type or "").lower(), "?")
            tgt_group = TYPE_GROUPS.get(
                (field.get("type") or "").lower(), "?")
            if src_group != "?" and tgt_group != "?" and src_group != tgt_group:
                transform = "cast"
            suggestions.append(
                MappingEntry(
                    source=src.name,
                    target=path,
                    transform=transform,
                    confidence=confidence,
                    rationale=f"Token overlap with '{path}' ({field.get('description', '')[:60]})",
                )
            )
        else:
            suggestions.append(
                MappingEntry(
                    source=src.name,
                    target="",
                    transform="direct",
                    confidence=0.0,
                    rationale="No confident match - map manually or leave unmapped.",
                )
            )
    return suggestions


_SUGGEST_SYSTEM = (
    "You map source columns to a target standard entity. "
    "Return a JSON object {\"mappings\": [...]} where each item has keys: "
    "source, target (exact target path or empty string), transform "
    "(direct|cast|concat|expression), expression (optional SQL expression), "
    "confidence (0-1), rationale (short)."
)


def suggest_llm(source: SourceSchema, entity: dict) -> Optional[list[MappingEntry]]:
    fields = entity.get("fields", [])
    if len(fields) > LLM_TARGET_CAP:
        # Pre-rank with the offline heuristic so the prompt stays bounded;
        # required targets are always included.
        scored = []
        for f in fields:
            best = max((_score(s.name, f)[0] for s in source.fields), default=0.0)
            scored.append((best, f))
        scored.sort(key=lambda item: item[0], reverse=True)
        top = [f for _, f in scored[:LLM_TARGET_CAP]]
        seen = {f.get("path") for f in top}
        fields = top + [f for f in fields
                        if f.get("required") and f.get("path") not in seen]
    targets = [
        {"path": f.get("path"), "type": f.get("type"),
         "required": f.get("required"), "description": f.get("description")}
        for f in fields
    ]
    user = json_dumps({
        "entity": entity.get("name"),
        "targets": targets,
        "source_fields": [
            {"name": f.name, "type": f.type, "sample": f.sample}
            for f in source.fields
        ],
    })
    data = chat_json(_SUGGEST_SYSTEM, user)
    if not data or "mappings" not in data:
        return None
    out = []
    for m in data["mappings"]:
        try:
            out.append(MappingEntry(**{k: m.get(k) for k in (
                "source", "target", "transform", "expression",
                "confidence", "rationale") if m.get(k) is not None}))
        except Exception:
            continue
    return out or None


def suggest_mappings(source: SourceSchema, entity: dict) -> dict[str, Any]:
    """Return {mappings, generated_by}. LLM first when configured."""
    if llm_enabled():
        llm_result = suggest_llm(source, entity)
        if llm_result is not None:
            return {"mappings": llm_result, "generated_by": "meta",
                    "model": model_name(), "llm_error": None}
        err = last_error() or "LLM returned no usable mappings"
        return {"mappings": suggest_offline(source, entity),
                "generated_by": "offline-demo", "model": "offline-heuristic",
                "llm_error": err}
    return {"mappings": suggest_offline(source, entity),
            "generated_by": "offline-demo", "model": "offline-heuristic",
            "llm_error": None}


import json as _json  # noqa: E402
from typing import Optional  # noqa: E402


def json_dumps(obj: Any) -> str:
    return _json.dumps(obj, default=str)
