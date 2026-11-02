"""Minimal MCP client over stdio (JSON-RPC 2.0, newline-delimited).

Speaks enough of the Model Context Protocol to call tools on any
standards MCP server: ``initialize`` -> ``tools/list`` -> ``tools/call``.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, command: list[str]):
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._next_id = 0

    def _rpc(self, method: str, params: Optional[dict] = None) -> Any:
        assert self._proc.stdin and self._proc.stdout
        self._next_id += 1
        request = {"jsonrpc": "2.0", "id": self._next_id,
                   "method": method, "params": params or {}}
        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise MCPError("MCP server closed stdout")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MCPError(f"Invalid JSON-RPC response: {exc}") from exc
        if "error" in response:
            raise MCPError(str(response["error"].get("message", response["error"])))
        return response.get("result")

    def initialize(self) -> None:
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "telemetry-foundry", "version": "0.1.0"},
        })
        assert self._proc.stdin
        self._proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self._proc.stdin.flush()

    def list_tools(self) -> list[dict]:
        result = self._rpc("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> Any:
        result = self._rpc("tools/call",
                           {"name": name, "arguments": arguments or {}})
        chunks = result.get("content", []) if isinstance(result, dict) else []
        texts = [c.get("text", "") for c in chunks if c.get("type") == "text"]
        text = "\n".join(texts)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass

    def __enter__(self) -> "MCPClient":
        self.initialize()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


from typing import Optional  # noqa: E402
