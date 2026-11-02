"""Fetch all OCSF event classes and convert them to registry definitions.

Source: https://schema.ocsf.io (pinned version below).
Output: one standard per OCSF category in ``backend/data/standards/``,
plus the smallest category held back in ``backend/mcp_server/sources/``
so it is only reachable through MCP hydration.

Run: ``python backend/scripts/fetch_ocsf.py`` (stdlib only).
Re-run to upgrade the snapshot; the version constant is the pin.
"""

import html
import json
import re
import sys
import urllib.request
from pathlib import Path

OCSF_VERSION = "1.9.0"
API = f"https://schema.ocsf.io/api/{OCSF_VERSION}"

BACKEND = Path(__file__).resolve().parent.parent
ACTIVE_DIR = BACKEND / "data" / "standards"
MCP_SOURCES = BACKEND / "mcp_server" / "sources"

TYPE_MAP = {
    "timestamp_t": "datetime",
    "datetime_t": "datetime",
    "date_t": "date",
    "time_t": "string",
    "integer_t": "integer",
    "long_t": "integer",
    "unsigned_long_t": "integer",
    "port_t": "integer",
    "float_t": "decimal",
    "double_t": "decimal",
    "boolean_t": "boolean",
    "string_t": "string",
    "json_t": "string",
    "subnet_t": "subnet",
    "ip_t": "ip",
    "mac_t": "mac",
    "email_t": "email",
}


def norm_type(ocsf_type: str) -> str:
    if ocsf_type in TYPE_MAP:
        return TYPE_MAP[ocsf_type]
    base = ocsf_type[:-2] if ocsf_type.endswith("_t") else ocsf_type
    return base or "string"


def clean(text: str, limit: int = 400) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def get(path: str) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"Accept": "application/json",
                 "User-Agent": "telemetry-foundry/0.1 (+schema snapshot)"},
    )
    last: Exception | None = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # retry transient failures
            last = exc
    raise RuntimeError(f"GET {API}{path} failed: {last}")


def fetch_object(object_type: str, objects: dict, extensions: list[str]) -> dict:
    if object_type in objects:
        return objects[object_type]
    paths = [f"/objects/{object_type}"]
    paths += [f"/objects/{ext}/{object_type}" for ext in extensions]
    for path in paths:
        try:
            objects[object_type] = get(path)
            return objects[object_type]
        except Exception:
            continue
    print(f"  object {object_type}: not found, kept flat")
    objects[object_type] = {}
    return {}


def convert_class(detail: dict, objects: dict, extensions: list[str]) -> list[dict]:
    """Flatten one class's attributes to registry fields (one object level)."""
    fields = []
    for name, attr in detail.get("attributes", {}).items():
        if "@deprecated" in attr:
            continue
        req = attr.get("requirement") == "required"
        if attr.get("type") == "object_t" and attr.get("object_type"):
            obj = fetch_object(attr["object_type"], objects, extensions)
            sub_attrs = obj.get("attributes", {})
            if not sub_attrs:
                fields.append({"path": name, "type": "object",
                               "required": req,
                               "description": clean(attr.get("description", ""))})
                continue
            for sub, sattr in sub_attrs.items():
                if "@deprecated" in sattr:
                    continue
                stype = ("object" if sattr.get("type") == "object_t"
                         else norm_type(sattr.get("type", "string_t")))
                fields.append({
                    "path": f"{name}.{sub}",
                    "type": stype,
                    # Nested attributes are only required when the parent
                    # object itself is required (OCSF conditional requirement).
                    "required": req and sattr.get("requirement") == "required",
                    "description": clean(sattr.get("description", "")),
                })
        else:
            fields.append({
                "path": name,
                "type": norm_type(attr.get("type", "string_t")),
                "required": req,
                "description": clean(attr.get("description", "")),
            })
    return fields


def main() -> None:
    print(f"Fetching OCSF {OCSF_VERSION} class list...")
    classes = get("/classes")
    live = [c for c in classes
            if "@deprecated" not in c and c.get("name") != "base_event"]
    print(f"{len(classes)} classes listed, {len(live)} active (non-deprecated).")

    by_category: dict[str, dict] = {}
    objects: dict = {}
    try:
        ext_data = get("/extensions")
        extensions = [e.get("name") for e in (
            ext_data if isinstance(ext_data, list) else ext_data.get("extensions", []))
            if e.get("name")]
    except Exception:
        extensions = []
    print(f"Extensions: {extensions or 'none found (core only)'}")
    for i, cls in enumerate(live, 1):
        print(f"[{i}/{len(live)}] {cls['name']}...")
        ext = cls.get("extension")
        path = f"/classes/{ext}/{cls['name']}" if ext else f"/classes/{cls['name']}"
        detail = get(path)
        fields = convert_class(detail, objects, extensions)
        cat = cls.get("category", "other")
        cat_name = cls.get("category_name") or cat.replace("_", " ").title()
        std = by_category.setdefault(cat, {
            "id": f"ocsf-{cat.replace('_', '-')}",
            "name": f"OCSF {cat_name}",
            "category": "security",
            "version": OCSF_VERSION,
            "description": (
                f"OCSF {OCSF_VERSION} {cat_name} event classes "
                f"(snapshot from schema.ocsf.io)."
            ),
            "entities": [],
        })
        std["entities"].append({
            "id": cls["name"],
            "name": cls.get("caption") or cls["name"],
            "fields": fields,
            "meta": {"class_uid": cls.get("uid"),
                     "category_uid": cls.get("category_uid")},
        })

    # Flagship classes first so each category opens on its primary event.
    flagship = {"network_activity", "authentication", "process_activity",
                "detection_finding", "api_activity", "inventory_info",
                "remediation_activity", "drone_flights_activity"}
    for std in by_category.values():
        std["entities"].sort(
            key=lambda e: (e["id"] not in flagship, e["id"]))

    # Hold back the smallest category for MCP-only hydration.
    heldback = min(by_category.values(), key=lambda s: len(s["entities"]))
    print(f"Holding back '{heldback['id']}' "
          f"({len(heldback['entities'])} classes) for MCP hydration.")
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    MCP_SOURCES.mkdir(parents=True, exist_ok=True)
    for path in ACTIVE_DIR.glob("ocsf-*.json"):
        path.unlink()
    for path in MCP_SOURCES.glob("ocsf-*.json"):
        path.unlink()
    total_fields = 0
    for std in sorted(by_category.values(), key=lambda s: s["id"]):
        dest = (MCP_SOURCES if std["id"] == heldback["id"] else ACTIVE_DIR)
        (dest / f"{std['id']}.json").write_text(
            json.dumps(std, indent=1), encoding="utf-8")
        n = sum(len(e["fields"]) for e in std["entities"])
        total_fields += n
        print(f"  {dest.name}/{std['id']}.json: "
              f"{len(std['entities'])} classes, {n} fields")
    print(f"Done: {len(by_category)} standards, {total_fields} fields total.")


if __name__ == "__main__":
    sys.exit(main())
