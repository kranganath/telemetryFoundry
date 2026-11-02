"""Demo MCP server (stdio) exposing standards as tools.

Tools:
  - list_standards: [{id, name, category, version, description}]
  - get_standard {standard_id}: full standard definition

Backed by ``sources/*.json`` next to this file. Swap the command
(``STANDARDS_MCP_COMMAND``) to point hydration at any real MCP server
with the same two tools.
"""

import json
import sys
from pathlib import Path

SOURCES = Path(__file__).resolve().parent / "sources"


def _load_all() -> dict:
    defs = {}
    if SOURCES.exists():
        for path in sorted(SOURCES.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                defs[doc["id"]] = doc
            except Exception:
                continue
    return defs


def _text(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def handle(request: dict, defs: dict) -> dict | None:
    method = request.get("method")
    req_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "telemetry-mcp", "version": "0.1.0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": [
            {"name": "list_standards",
             "description": "List available standard definitions.",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "get_standard",
             "description": "Fetch one full standard definition by id.",
             "inputSchema": {"type": "object",
                             "properties": {"standard_id": {"type": "string"}},
                             "required": ["standard_id"]}},
        ]}}
    if method == "tools/call":
        params = request.get("params", {})
        name, args = params.get("name"), params.get("arguments", {})
        if name == "list_standards":
            items = [{k: d.get(k) for k in
                      ("id", "name", "category", "version", "description")}
                     for d in defs.values()]
            return {"jsonrpc": "2.0", "id": req_id, "result": _text(items)}
        if name == "get_standard":
            doc = defs.get(args.get("standard_id"))
            if doc is None:
                return {"jsonrpc": "2.0", "id": req_id, "error": {
                    "code": -32000, "message": "Unknown standard"}}
            return {"jsonrpc": "2.0", "id": req_id, "result": _text(doc)}
        return {"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32601, "message": f"Unknown tool '{name}'"}}
    return {"jsonrpc": "2.0", "id": req_id, "error": {
        "code": -32601, "message": f"Unknown method '{method}'"}}


def main() -> None:
    defs = _load_all()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request, defs)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
