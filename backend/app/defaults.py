"""Default constant suggestions for required targets missing from the source.

Raw log extracts rarely carry OCSF metadata columns (uids, timestamps,
product labels). This closes that gap with one-click constants: values that
are derivable (class/category/type uids, schema version) are filled in, and
everything else gets a typed placeholder. Every suggestion carries low
confidence and a review rationale so the data owner sees what was assumed.
"""

from __future__ import annotations

from .models import MappingEntry


def _default_for(field: dict, standard: dict, entity: dict) -> tuple[str, str]:
    path = field.get("path", "")
    ftype = (field.get("type") or "").lower()
    meta = entity.get("meta", {}) or {}
    review = "Auto-fill default - review before approval"
    if path == "type_uid" and meta.get("class_uid") is not None:
        return (str(meta["class_uid"]),
                f"{review} (class UID; refine per activity if known)")
    if path == "class_uid" and meta.get("class_uid") is not None:
        return str(meta["class_uid"]), f"{review} (OCSF class UID)"
    if path == "category_uid" and meta.get("category_uid") is not None:
        return str(meta["category_uid"]), f"{review} (OCSF category UID)"
    if path == "time":
        return "current_timestamp()", f"{review} (ingest time)"
    if path == "metadata.version":
        return f"'{standard.get('version', '')}'", f"{review} (OCSF version)"
    if path == "metadata.product":
        return "'unknown'", f"{review} (set the logging product name)"
    if ftype == "boolean":
        return "false", review
    if ftype in ("integer", "number"):
        return "0", review
    if ftype == "decimal":
        return "0.0", review
    if ftype in ("datetime", "timestamp"):
        return "current_timestamp()", review
    if ftype == "date":
        return "current_date()", review
    return "''", review


def suggest_defaults(standard: dict, entity: dict,
                     mappings: list[MappingEntry]) -> list[MappingEntry]:
    """Build constant mappings for required targets with no mapping yet."""
    mapped = {m.target for m in mappings if m.target}
    out = []
    for f in entity.get("fields", []):
        if not f.get("required") or f.get("path") in mapped:
            continue
        expr, rationale = _default_for(f, standard, entity)
        out.append(MappingEntry(
            source="", target=f["path"], transform="constant",
            expression=expr, confidence=0.3, rationale=rationale,
        ))
    return out
