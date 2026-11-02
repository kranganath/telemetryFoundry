"""Deploy approved artifacts to a Databricks workspace (REST API).

Steps: ensure the Unity Catalog schema, import the bundle files into the
workspace, and execute the generated SQL on a SQL warehouse. Credentials
arrive per request and are never stored or logged.
"""

from __future__ import annotations

import base64
from typing import Any, Optional

import httpx


class DeployError(RuntimeError):
    pass


def _client(host: str, token: str,
            client: Optional[httpx.Client] = None) -> httpx.Client:
    if client is not None:
        return client
    return httpx.Client(base_url=host.rstrip("/"), timeout=120.0,
                        headers={"Authorization": f"Bearer {token}"})


_HINTS = {
    "CATALOG_DOES_NOT_EXIST": "Check the Catalog name in the export form "
                              "above (or create it in Catalog Explorer).",
    "SCHEMA_DOES_NOT_EXIST": "Check the Schema name in the export form above.",
    "RESOURCE_DOES_NOT_EXIST": "A named workspace object was not found — "
                               "check catalog, schema, and warehouse id.",
    "PERMISSION_DENIED": "The token lacks permission for this action.",
    "UNAUTHENTICATED": "Authentication failed — check the token.",
}


def _friendly(action: str, resp: httpx.Response) -> str:
    try:
        detail = resp.json()
    except Exception:
        detail = resp.text[:300]
    code = detail.get("error_code", "") if isinstance(detail, dict) else ""
    message = (detail.get("message", "") if isinstance(detail, dict)
               else str(detail)[:300])
    hint = _HINTS.get(code, "")
    if not hint and resp.status_code == 401:
        hint = "Authentication failed — check the token."
    elif not hint and resp.status_code == 403:
        hint = "Permission denied — the token lacks the required access."
    parts = f"{action} failed ({resp.status_code})"
    if code:
        parts += f" [{code}]"
    if message:
        parts += f": {message}"
    if hint:
        parts += f" {hint}"
    return parts


_ALREADY_EXISTS = {"SCHEMA_ALREADY_EXISTS", "CATALOG_ALREADY_EXISTS",
                   "RESOURCE_ALREADY_EXISTS", "DIRECTORY_ALREADY_EXISTS"}


def _ok(resp: httpx.Response, action: str) -> dict:
    if resp.status_code in (200, 201):
        try:
            return resp.json()
        except Exception:
            return {}
    if resp.status_code == 409:
        return {"already_exists": True}
    try:
        detail = resp.json()
    except Exception:
        detail = None
    # Unity Catalog often signals "already exists" as 400 + error code.
    if isinstance(detail, dict) and detail.get("error_code") in _ALREADY_EXISTS:
        return {"already_exists": True}
    raise DeployError(_friendly(action, resp))


def ensure_schema(client: httpx.Client, catalog: str, schema: str) -> dict:
    resp = client.post("/api/2.1/unity-catalog/schemas",
                       json={"name": schema, "catalog_name": catalog})
    return _ok(resp, "create schema")


def ensure_directory(client: httpx.Client, path: str) -> dict:
    resp = client.post("/api/2.0/workspace/mkdirs", json={"path": path})
    return _ok(resp, f"create directory {path}")


_NOTEBOOK_LANGUAGES = {".py": "PYTHON", ".sql": "SQL",
                        ".scala": "SCALA", ".r": "R"}


def import_file(client: httpx.Client, workspace_path: str,
                content: str) -> dict:
    ext = "." + workspace_path.rsplit(".", 1)[-1].lower() \
        if "." in workspace_path else ""
    payload = {
        "path": workspace_path,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "overwrite": True,
    }
    language = _NOTEBOOK_LANGUAGES.get(ext)
    if language:
        # Notebook formats require an explicit language; without it the
        # server misreads the upload (e.g. as an archive).
        payload["format"] = "SOURCE"
        payload["language"] = language
    else:
        payload["format"] = "AUTO"
    resp = client.post("/api/2.0/workspace/import", json=payload)
    return _ok(resp, f"import {workspace_path}")


def run_statement(client: httpx.Client, warehouse_id: str, statement: str,
                  catalog: str, schema: str) -> dict:
    resp = client.post("/api/2.0/sql/statements", json={
        "warehouse_id": warehouse_id,
        "catalog": catalog,
        "schema": schema,
        "statement": statement,
        "wait_timeout": "50s",
    })
    body = _ok(resp, "execute SQL")
    status = ((body.get("status") or {}).get("state") or "UNKNOWN")
    return {"statement_id": body.get("statement_id"),
            "status": status, "body": body}


def deploy(host: str, token: str, warehouse_id: str, catalog: str,
           schema: str, workspace_dir: str, files: list[dict],
           main_sql: str, run_sql: bool = True,
           client: Optional[httpx.Client] = None) -> dict[str, Any]:
    """Run the full deployment. Raises DeployError with a safe message."""
    if not host or not token:
        raise DeployError("Databricks host and token are required")
    owned = client is None
    client = _client(host, token, client)
    try:
        schema_result = ensure_schema(client, catalog, schema)
        ensure_directory(client, workspace_dir)
        imported = []
        for f in files:
            path = f"{workspace_dir.rstrip('/')}/{f['path']}"
            import_file(client, path, f["content"])
            imported.append(path)
        statement = None
        if run_sql:
            if not warehouse_id:
                raise DeployError("warehouse_id is required to execute SQL")
            statement = run_statement(client, warehouse_id, main_sql,
                                      catalog, schema)
        return {"schema": schema_result, "imported": imported,
                "statement": statement}
    except DeployError:
        raise
    except Exception as exc:
        raise DeployError(f"deployment failed: {exc}") from exc
    finally:
        if owned:
            client.close()
