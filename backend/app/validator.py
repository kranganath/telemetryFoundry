"""Mapping validation against the target entity definition."""

from __future__ import annotations

from .models import MappingEntry, SourceSchema

# Loose compatibility groups for warnings (not errors).
TYPE_GROUPS = {
    "string": "text", "text": "text", "iban": "text", "bic": "text",
    "code": "text", "id": "text", "uri": "text", "email": "text",
    "ip": "text", "mac": "text", "subnet": "text", "hostname": "text",
    "version": "text", "country": "text", "city": "text", "json": "text",
    "decimal": "number", "number": "number", "integer": "number",
    "boolean": "bool",
    "date": "date", "datetime": "date", "timestamp": "date",
}


def validate_mappings(
    source: SourceSchema,
    entity: dict,
    mappings: list[MappingEntry],
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    src_types = {f.name: f.type for f in source.fields}
    targets = {f.get("path"): f for f in entity.get("fields", [])}

    seen_targets: dict[str, str] = {}
    for m in mappings:
        if m.transform == "constant":
            if not (m.expression or "").strip():
                errors.append(
                    f"Constant mapping for '{m.target or '(no target)'}' "
                    f"needs an expression."
                )
            if m.source and m.source not in src_types:
                errors.append(f"Unknown source field '{m.source}'.")
        elif m.source not in src_types:
            errors.append(f"Unknown source field '{m.source}'.")
        if not m.target:
            warnings.append(f"Source field '{m.source}' is unmapped.")
            continue
        if m.target not in targets:
            errors.append(f"Unknown target path '{m.target}'.")
            continue
        if m.target in seen_targets:
            errors.append(
                f"Target '{m.target}' mapped twice "
                f"('{seen_targets[m.target]}' and '{m.source}')."
            )
        else:
            seen_targets[m.target] = m.source
        if m.confidence < 0.5 and m.transform != "constant":
            warnings.append(
                f"Low confidence ({m.confidence}) for "
                f"'{m.source}' -> '{m.target}'."
            )
        if m.source not in src_types:
            continue  # already reported above; nothing to compare
        if m.transform in ("cast", "expression"):
            continue  # explicit conversion already handles the mismatch
        src_group = TYPE_GROUPS.get(src_types.get(m.source, "").lower(), "?")
        tgt_group = TYPE_GROUPS.get(targets[m.target].get("type", "").lower(), "?")
        if src_group != "?" and tgt_group != "?" and src_group != tgt_group:
            warnings.append(
                f"Possible type mismatch: '{m.source}' ({src_types[m.source]}) "
                f"-> '{m.target}' ({targets[m.target].get('type')})."
            )

    mapped = {m.target for m in mappings if m.target}
    required_paths = {p for p, f in targets.items() if f.get("required")}
    for path in sorted(required_paths):
        if path not in mapped:
            errors.append(f"Required target '{path}' has no mapping.")

    # Coverage tracks required targets (the approval gate); breadth tracks
    # overall attribute usage across wide schemas like OCSF.
    if required_paths:
        coverage = round(len(mapped & required_paths) / len(required_paths), 3)
    else:
        coverage = 1.0
    breadth = round(len(mapped & set(targets)) / max(len(targets), 1), 3)
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "coverage": coverage,
        "breadth": breadth,
        "required_total": len(required_paths),
        "mapped_targets": sorted(mapped & set(targets)),
    }
