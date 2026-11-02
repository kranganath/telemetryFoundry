"""Standards hydration pipeline: MCP fetch -> pending review -> activate.

Two fetch paths, same destination:

- ``mcp-direct`` (offline-friendly): call the MCP ``get_standard`` tool and
  validate the payload against :class:`StandardDefinition`.
- ``mcp-agent`` (Meta LLM configured): a small tool-use loop where the model
  may call ``list_standards`` / ``get_standard`` and must return the final
  definition normalized to the registry schema. Falls back to direct fetch.

Hydrated standards land in ``data/standards_pending/`` and only join the
active registry after human activation, mirroring the approve-before-export
gate used for mappings.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

from .llm import chat, llm_enabled, model_name
from .mcp_client import MCPClient, MCPError
from .models import StandardDefinition
from . import standards as registry

APP_DIR = Path(__file__).resolve().parent
DEFAULT_SERVER = APP_DIR.parent / "mcp_server" / "standards_server.py"
PENDING_DIR = APP_DIR.parent / "data" / "standards_pending"

MAX_AGENT_STEPS = 4

_AGENT_SYSTEM = """You fetch standard definitions through MCP tools and normalize them.
Reply with exactly one JSON object per turn, either:
{"tool": "list_standards", "arguments": {}}
{"tool": "get_standard", "arguments": {"standard_id": "..."}}
{"done": true, "standard": {...}}
The final "standard" must match this schema:
{"id": str, "name": str, "category": str, "version": str, "description": str,
 "entities": [{"id": str, "name": str, "fields":
   [{"path": str, "type": str, "required": bool, "description": str}]}]}
Preserve every field from the source; never invent fields."""


def mcp_command() -> list[str]:
    override = os.environ.get("STANDARDS_MCP_COMMAND", "").strip()
    if override:
        return override.split()
    return [sys.executable, str(DEFAULT_SERVER)]


def _validate(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise MCPError("MCP payload is not a JSON object")
    return StandardDefinition(**payload).model_dump()


def fetch_direct(standard_id: str) -> dict:
    try:
        with MCPClient(mcp_command()) as client:
            return _validate(client.call_tool("get_standard", {"standard_id": standard_id}))
    except MCPError as exc:
        if "Unknown standard" in str(exc):
            raise UnknownStandardError(standard_id) from exc
        raise


def fetch_with_agent(standard_id: str) -> tuple[dict, bool]:
    """LLM tool-use loop over MCP tools; falls back to direct fetch.

    Returns (definition, via_agent).
    """
    try:
        with MCPClient(mcp_command()) as client:
            messages = [
                {"role": "system", "content": _AGENT_SYSTEM},
                {"role": "user", "content": f"Fetch standard '{standard_id}'. "
                 "List first if you are unsure of the exact id."},
            ]
            for _ in range(MAX_AGENT_STEPS):
                content = chat(messages, max_tokens=6000)
                if not content:
                    break
                messages.append({"role": "assistant", "content": content})
                try:
                    action = json.loads(content)
                except json.JSONDecodeError:
                    messages.append({"role": "user", "content":
                        "Reply with exactly one JSON object."})
                    continue
                if action.get("done") and "standard" in action:
                    return _validate(action["standard"]), True
                if action.get("tool") in ("list_standards", "get_standard"):
                    try:
                        obs = client.call_tool(action["tool"], action.get("arguments", {}))
                    except MCPError as exc:
                        obs = {"error": str(exc)}
                    messages.append({"role": "user", "content":
                        f"Tool result: {json.dumps(obs, default=str)[:6000]}"})
                else:
                    messages.append({"role": "user", "content":
                        "Unknown action. Use list_standards, get_standard, or done."})
    except UnknownStandardError:
        raise
    except Exception:
        pass
    return fetch_direct(standard_id), False


def hydrate(standard_id: str) -> dict:
    """Fetch a standard via MCP into the pending queue."""
    if llm_enabled():
        definition, via_agent = fetch_with_agent(standard_id)
    else:
        definition, via_agent = fetch_direct(standard_id), False
    fetched_by = "mcp-agent" if via_agent else "mcp-direct"
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    path = PENDING_DIR / f"{definition['id']}.json"
    path.write_text(json.dumps(definition, indent=2), encoding="utf-8")
    return {"id": definition["id"], "name": definition.get("name"),
            "status": "pending", "fetched_by": fetched_by,
            "model": model_name() if via_agent else None}


def list_pending() -> list[dict]:
    if not PENDING_DIR.exists():
        return []
    out = []
    for path in sorted(PENDING_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            out.append({"id": doc.get("id"), "name": doc.get("name"),
                        "category": doc.get("category"),
                        "status": "pending"})
        except (json.JSONDecodeError, OSError):
            continue
    return out


def activate(standard_id: str) -> dict:
    """Move a pending standard into the active registry (human gate)."""
    src = PENDING_DIR / f"{standard_id}.json"
    if not src.exists():
        raise UnknownStandardError(standard_id)
    definition = _validate(json.loads(src.read_text(encoding="utf-8")))
    registry.DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = registry.DATA_DIR / f"{definition['id']}.json"
    shutil.move(str(src), str(dest))
    registry.refresh()
    return {"id": definition["id"], "name": definition.get("name"),
            "status": "active"}


class UnknownStandardError(KeyError):
    pass
