"""Agentic layer: classifier, critic, and onboarding supervisor.

Specialists do one job (classify, map, fix, validate); the supervisor
chains them toward a complete, validated proposal. Agents propose, owners
dispose: approval and deployment are never automated.
"""

from __future__ import annotations

import json
from typing import Any

from . import standards as registry
from .defaults import suggest_defaults
from .evaluator import dry_run as run_dryrun
from .exporter import spark_type
from .llm import chat_json, llm_enabled, model_name
from .mapper import _tokens, suggest_mappings
from .models import MappingEntry, SourceSchema
from .validator import validate_mappings

CLASSIFY_SYSTEM = (
    "Pick the best target standard entity for the source columns. Return a "
    "JSON object {standard_id, entity_id, confidence (0-1), rationale}. "
    "Use exact ids from the candidates."
)

CRITIC_SYSTEM = (
    "Fix the mapping errors. Return a JSON object {mappings: [...]} with the "
    "FULL corrected list using this schema per item: source, target, "
    "transform (direct|cast|expression|constant), expression, confidence, "
    "rationale. Only use target paths from the provided list. Prefer fixing "
    "targets and transforms over dropping rows."
)


def _step(trace: list, agent: str, action: str, detail: str = "",
          status: str = "ok") -> None:
    trace.append({"agent": agent, "action": action, "detail": detail,
                  "status": status})


# -- Classifier ----------------------------------------------------------


def _entity_index(entity: dict) -> list[dict]:
    index = []
    for f in entity.get("fields", []):
        path = f.get("path", "")
        index.append({
            "path": path,
            "name_tokens": _tokens(path.replace(".", " ")),
            "desc_tokens": _tokens(f.get("description", "")),
            "leaf_tokens": _tokens(path.split(".")[-1]),
        })
    return index


def _indexed_score(src_tokens: set[str], entry: dict) -> float:
    hit = src_tokens & entry["name_tokens"]
    score = 0.0
    if hit:
        score = (len(hit) / max(len(src_tokens | entry["name_tokens"]), 1)
                 + 0.3 * len(hit))
    elif src_tokens & entry["desc_tokens"]:
        score = 0.25
    if entry["leaf_tokens"] & src_tokens:
        score += 0.5
    return score


def classify_offline(source: SourceSchema) -> Optional[dict]:
    src_list = [(f.name, _tokens(f.name)) for f in source.fields]
    if not src_list:
        return None
    ranked = []
    for std_order, std in enumerate(registry.load_standards()):
        for ent_order, ent in enumerate(std.get("entities", [])):
            index = _entity_index(ent)
            total = 0.0
            for _, tokens in src_list:
                total += max((_indexed_score(tokens, e) for e in index),
                             default=0.0)
            ranked.append((total / len(src_list), std_order, ent_order,
                           std.get("id"), ent.get("id"), ent.get("name")))
    if not ranked:
        return None
    ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
    raw_top = ranked[0]
    # Near-ties (within noise) go to the primary class of the best standard:
    # specificity must earn its win with distinctly better evidence.
    contenders = [r for r in ranked
                  if r[3] == raw_top[3] and raw_top[0] - r[0] <= 0.05]
    pick = min(contenders, key=lambda r: r[2])
    tied = pick != raw_top
    return {
        "standard_id": pick[3], "entity_id": pick[4], "entity_name": pick[5],
        "confidence": round(min(0.95, pick[0]), 2),
        "rationale": ("Scores within noise margin; preferred primary class."
                      if tied else
                      "Highest token overlap across registry entities."),
        "alternatives": [
            {"standard_id": r[3], "entity_id": r[4],
             "score": round(r[0], 2)} for r in ranked[1:4]
        ],
    }


def classify_llm(source: SourceSchema) -> Optional[dict]:
    candidates = []
    for std in registry.load_standards():
        for ent in std.get("entities", []):
            candidates.append({
                "standard_id": std.get("id"), "entity_id": ent.get("id"),
                "name": ent.get("name"),
                "required": [f.get("path") for f in ent.get("fields", [])
                             if f.get("required")],
            })
    data = chat_json(CLASSIFY_SYSTEM, json.dumps({
        "candidates": candidates,
        "source_fields": [{"name": f.name, "type": f.type}
                          for f in source.fields],
    }, default=str))
    if not data:
        return None
    _, entity = registry.get_entity(data.get("standard_id"),
                                    data.get("entity_id"))
    if not entity:
        return None
    return {
        "standard_id": data.get("standard_id"),
        "entity_id": data.get("entity_id"), "entity_name": entity.get("name"),
        "confidence": float(data.get("confidence", 0.5)),
        "rationale": data.get("rationale", ""),
        "alternatives": [],
    }


def classify_source(source: SourceSchema) -> dict:
    """Return {selection, generated_by, model} with offline fallback."""
    if llm_enabled():
        selection = classify_llm(source)
        if selection is not None:
            return {"selection": selection, "generated_by": "meta",
                    "model": model_name()}
    selection = classify_offline(source)
    if selection is None:
        raise ValueError("Could not classify: empty source or registry")
    return {"selection": selection, "generated_by": "offline-demo",
            "model": "offline-heuristic"}


# -- Critic ----------------------------------------------------------------


def critic_repairs(source: SourceSchema, standard: dict, entity: dict,
                   mappings: list[MappingEntry]) -> tuple[list[MappingEntry], list[str]]:
    """Deterministic fixes: drop invalid targets, dedupe, fill required."""
    notes: list[str] = []
    src_names = {f.name for f in source.fields}
    valid_paths = {f.get("path") for f in entity.get("fields", [])}
    kept: dict[str, MappingEntry] = {}
    survivors: list[MappingEntry] = []
    for m in mappings:
        if m.transform == "constant" and not (m.expression or "").strip():
            notes.append(f"Dropped constant '{m.target}' with no expression.")
            continue
        if m.target and m.target not in valid_paths:
            notes.append(f"Cleared unknown target '{m.target}'.")
            m = m.model_copy(update={"target": "", "confidence": 0.0,
                                     "rationale": "Critic: unknown target."})
        if m.target and m.target in kept:
            other = kept[m.target]
            if m.confidence <= other.confidence:
                notes.append(f"Removed duplicate mapping to '{m.target}'.")
                continue
            notes.append(f"Replaced duplicate mapping to '{m.target}'.")
            survivors = [s for s in survivors if s.target != m.target]
        if m.source and m.source not in src_names and m.transform != "constant":
            notes.append(f"Cleared unknown source '{m.source}'.")
            m = m.model_copy(update={"source": "", "target": "",
                                     "rationale": "Critic: unknown source."})
        if m.target:
            kept[m.target] = m
        survivors.append(m)
    before = len(survivors)
    added = suggest_defaults(standard, entity, survivors)
    mapped = {s.target for s in survivors if s.target}
    for a in added:
        if a.target not in mapped:
            survivors.append(a)
            mapped.add(a.target)
    if len(survivors) > before:
        notes.append(f"Auto-filled {len(survivors) - before} required targets.")
    if not notes:
        notes.append("No deterministic repairs applied.")
    return survivors, notes


def critic_llm(source: SourceSchema, entity: dict,
               mappings: list[MappingEntry], report: dict) -> Optional[list[MappingEntry]]:
    fields = entity.get("fields", [])
    required = [f for f in fields if f.get("required")]
    mapped_paths = {m.target for m in mappings if m.target}
    rest = [f for f in fields
            if not f.get("required") and f.get("path") not in mapped_paths]
    shortlist = ([{"path": f.get("path"), "type": f.get("type"),
                   "required": bool(f.get("required"))} for f in required]
                 + [{"path": f.get("path")} for f in fields
                    if f.get("path") in mapped_paths]
                 + [{"path": f.get("path")} for f in rest[:400]])
    data = chat_json(CRITIC_SYSTEM, json.dumps({
        "targets": shortlist,
        "source_fields": [f.name for f in source.fields],
        "mappings": [m.model_dump() for m in mappings],
        "errors": report.get("errors", []),
        "warnings": report.get("warnings", [])[:10],
    }, default=str))
    if not data or "mappings" not in data:
        return None
    valid_paths = {f.get("path") for f in fields}
    out = []
    for m in data["mappings"]:
        try:
            entry = MappingEntry(**{k: m.get(k) for k in (
                "source", "target", "transform", "expression",
                "confidence", "rationale") if m.get(k) is not None})
        except Exception:
            continue
        if entry.target and entry.target not in valid_paths:
            continue
        out.append(entry)
    return out or None


def refine_mappings(standard_id: str, entity_id: Optional[str],
                    source: SourceSchema, mappings: list[MappingEntry],
                    max_rounds: int = 3) -> dict:
    """Validate-and-fix loop. Returns mappings, validation, rounds, trace."""
    max_rounds = min(max(1, max_rounds), 5)
    standard, entity = registry.get_entity(standard_id, entity_id)
    if not entity or not standard:
        raise ValueError("Unknown standard or entity")
    trace: list[dict] = []
    current = list(mappings)
    rounds = 0
    for _ in range(max_rounds):
        rounds += 1
        report = validate_mappings(source, entity, current)
        _step(trace, "validator", f"round {rounds}",
              f"{len(report['errors'])} errors, "
              f"{len(report['warnings'])} warnings",
              "ok" if report["valid"] else "failed")
        if report["valid"]:
            break
        fixed = None
        if llm_enabled():
            fixed = critic_llm(source, entity, current, report)
            by = "meta" if fixed is not None else "offline-demo (fallback)"
        else:
            by = "offline-demo"
        if fixed is None:
            fixed, notes = critic_repairs(source, standard, entity, current)
            detail = f"{by}: " + " ".join(notes)
        else:
            detail = f"{by}: revised {len(fixed)} mappings."
        _step(trace, "critic", f"round {rounds}", detail)
        if [m.model_dump() for m in fixed] == [m.model_dump() for m in current]:
            current = fixed
            break
        current = fixed
    report = validate_mappings(source, entity, current)
    return {"mappings": [m.model_dump() for m in current],
            "validation": report, "rounds": rounds, "trace": trace}


# -- Supervisor --------------------------------------------------------------


def onboard_source(source: SourceSchema, rows: Optional[list[dict]] = None,
                   max_rounds: int = 3) -> dict:
    """Full proposal: classify, map, fill, refine, dry-run. Never approves."""
    trace: list[dict] = []
    classified = classify_source(source)
    sel = classified["selection"]
    standard, entity = registry.get_entity(sel["standard_id"],
                                           sel["entity_id"])
    assert entity is not None and standard is not None
    _step(trace, "classifier", "selected entity",
          f"{sel['standard_id']}/{sel['entity_id']} "
          f"({classified['generated_by']}, conf {sel['confidence']})")
    suggested = suggest_mappings(source, entity)
    _step(trace, "mapper", "drafted mappings",
          f"{len(suggested['mappings'])} rows via {suggested['generated_by']}")
    mappings = suggested["mappings"]
    added = suggest_defaults(standard, entity, mappings)
    mappings = mappings + added
    _step(trace, "defaults", "filled required gaps", f"{len(added)} constants")
    refined = refine_mappings(sel["standard_id"], sel["entity_id"],
                              source, mappings, max_rounds)
    trace.extend(refined["trace"])
    final = [MappingEntry(**m) for m in refined["mappings"]]
    dry = None
    if rows:
        target_types = {f["path"]: spark_type(f.get("type"))
                        for f in entity.get("fields", [])}
        dry = run_dryrun([m.model_dump() for m in final], rows[:200],
                         target_types)
        _step(trace, "dryrun", "previewed output",
              f"{len(dry['rows'])} rows, {dry['errors']} cell errors",
              "ok" if dry["errors"] == 0 else "failed")
    return {
        "standard_id": sel["standard_id"], "entity_id": sel["entity_id"],
        "classification": {**sel, "generated_by": classified["generated_by"],
                           "model": classified["model"]},
        "mappings": [m.model_dump() for m in final],
        "validation": refined["validation"],
        "dry_run": dry,
        "trace": trace,
    }


from typing import Optional  # noqa: E402
