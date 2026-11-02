"""API tests for Telemetry Foundry (offline demo mode, OCSF targets)."""

import os
import shutil

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import hydrator  # noqa: E402
from app import standards as registry  # noqa: E402
from app.llm import llm_enabled, model_name  # noqa: E402
from app.main import app  # noqa: E402

# Force offline mode for deterministic tests. This runs after the app
# imports above on purpose: importing app loads backend/.env, if present.
for var in ("META_API_BASE_URL", "META_API_KEY", "META_MODEL"):
    os.environ.pop(var, None)

client = TestClient(app)

STD, ENT = "ocsf-network", "network_activity"

SOURCE = {
    "name": "firewall_logs",
    "fields": [
        {"name": "event_time", "type": "string"},
        {"name": "src_ip", "type": "string"},
        {"name": "src_port", "type": "integer"},
        {"name": "dst_ip", "type": "string"},
        {"name": "dst_port", "type": "integer"},
        {"name": "protocol", "type": "string"},
        {"name": "action", "type": "string"},
        {"name": "bytes_sent", "type": "integer"},
        {"name": "app_name", "type": "string"},
        {"name": "rule_name", "type": "string"},
    ],
}

ACTIVE_IDS = {
    "ocsf-network", "ocsf-iam", "ocsf-system", "ocsf-findings",
    "ocsf-application", "ocsf-discovery", "ocsf-remediation",
}


def test_health_demo_mode():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["mode"] == "demo"


def test_llm_env_config(monkeypatch):
    monkeypatch.setenv("META_API_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("META_API_KEY", "test-key")
    monkeypatch.setenv("META_MODEL", "test-model")
    assert llm_enabled() is True
    assert model_name() == "test-model"
    assert client.get("/api/health").json() == {
        "status": "ok", "mode": "meta", "model": "test-model",
    }


def test_list_standards_security_only():
    r = client.get("/api/standards")
    ids = {s["id"] for s in r.json()["standards"]}
    assert ACTIVE_IDS <= ids
    # A user may legitimately activate MCP-only standards; the durable
    # invariant is that everything in the registry is security telemetry
    # (OCSF or OTel).
    assert all(i.startswith(("ocsf-", "otel-")) for i in ids)
    assert not any(i.startswith(("iso20022", "fhir", "nist", "gdpr"))
                   for i in ids)


def _suggest():
    r = client.post("/api/suggest", json={
        "standard_id": STD,
        "entity_id": ENT,
        "source": SOURCE,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["generated_by"] == "offline-demo"
    return body["mappings"]


def test_suggest_maps_logging_fields_to_ocsf():
    by_source = {m["source"]: m for m in _suggest()}
    assert by_source["src_ip"]["target"] == "src_endpoint.ip"
    assert by_source["action"]["target"] == "action"
    assert by_source["event_time"]["target"] == "time"
    # Type mismatches are flagged as conversions for the export.
    assert by_source["event_time"]["transform"] == "cast"
    assert by_source["src_port"]["transform"] == "direct"


def test_validate_catches_missing_required():
    r = client.post("/api/validate", json={
        "standard_id": STD,
        "entity_id": ENT,
        "source": SOURCE,
        "mappings": [{"source": "event_time", "target": "time"}],
    })
    body = r.json()
    assert body["valid"] is False
    assert any("Required target" in e for e in body["errors"])


def _complete_mappings():
    mappings = _suggest()
    detail = client.get(f"/api/standards/{STD}").json()
    entity = next(e for e in detail["entities"] if e["id"] == ENT)
    fields = entity["fields"]
    used = {m["target"] for m in mappings if m["target"]}
    names = [f["name"] for f in SOURCE["fields"]]
    i = 0
    for m in mappings:
        if not m["target"]:
            for cand in [f["path"] for f in fields if f["path"] not in used]:
                m["target"] = cand
                used.add(cand)
                break
    for f in fields:
        if f["required"] and f["path"] not in used:
            mappings.append({"source": names[i % len(names)],
                             "target": f["path"]})
            used.add(f["path"])
            i += 1
    return mappings


def test_approve_and_export_roundtrip():
    mappings = _complete_mappings()
    r = client.post("/api/approve", json={
        "standard_id": STD,
        "entity_id": ENT,
        "source": SOURCE,
        "mappings": mappings,
        "approved_by": "data-owner",
    })
    assert r.status_code == 200, r.text
    approval_id = r.json()["approval"]["id"]

    r = client.post("/api/export", json={
        "approval_id": approval_id,
        "target_catalog": "main",
        "target_schema": "curated",
        "output_table": "network_activity",
    })
    assert r.status_code == 200
    body = r.json()
    paths = {f["path"] for f in body["files"]}
    assert {"transform.py", "transform.sql", "dq_tests.py", "databricks.yml"} <= paths
    assert body["generated_by"] == "template"
    sql = next(f["content"] for f in body["files"] if f["path"] == "transform.sql")
    assert "CREATE OR REPLACE TABLE main.curated.network_activity" in sql
    # The string->timestamp conversion survives into the deployment.
    assert "CAST(`event_time` AS TIMESTAMP)" in sql


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """Redirect active + pending registry dirs so hydration tests stay clean."""
    active = tmp_path / "standards"
    pending = tmp_path / "pending"
    active.mkdir()
    for path in registry.DATA_DIR.glob("*.json"):
        shutil.copy(path, active / path.name)
    # Start from the held-back state even if a user activated it for real.
    for path in active.glob("ocsf-unmanned-systems.json"):
        path.unlink()
    monkeypatch.setattr(registry, "DATA_DIR", active)
    monkeypatch.setattr(hydrator, "PENDING_DIR", pending)
    registry.refresh()
    yield {"active": active, "pending": pending}
    registry.refresh()


def _defaults(mappings):
    r = client.post("/api/defaults", json={
        "standard_id": STD,
        "entity_id": ENT,
        "source": SOURCE,
        "mappings": mappings,
    })
    assert r.status_code == 200
    return r.json()["added"]


def test_defaults_fill_missing_required_with_uids():
    added = {m["target"]: m for m in _defaults(_suggest())}
    detail = client.get(f"/api/standards/{STD}").json()
    entity = next(e for e in detail["entities"] if e["id"] == ENT)
    required = {f["path"] for f in entity["fields"] if f["required"]}
    suggested = {m["target"] for m in _suggest() if m["target"]}
    assert set(added) == required - suggested
    assert all(m["transform"] == "constant" for m in added.values())
    assert added["class_uid"]["expression"] == str(entity["meta"]["class_uid"])
    assert added["type_uid"]["expression"] == str(entity["meta"]["class_uid"])
    assert added["metadata.version"]["expression"] == f"'{detail['version']}'"


def test_coverage_tracks_required_targets():
    r = client.post("/api/validate", json={
        "standard_id": STD,
        "entity_id": ENT,
        "source": SOURCE,
        "mappings": [{"source": "event_time", "target": "time"}],
    })
    body = r.json()
    assert body["coverage"] == round(1 / body["required_total"], 3)
    assert body["breadth"] < body["coverage"]  # 630-field class, 1 mapping


def test_cast_suppresses_mismatch_warning():
    payload = {
        "standard_id": STD,
        "entity_id": ENT,
        "source": SOURCE,
        "mappings": [{"source": "event_time", "target": "time",
                      "transform": "cast"}],
    }
    r = client.post("/api/validate", json=payload)
    assert not any("mismatch" in w for w in r.json()["warnings"])
    payload["mappings"][0]["transform"] = "direct"
    r = client.post("/api/validate", json=payload)
    assert any("mismatch" in w for w in r.json()["warnings"])


def test_constant_mapping_needs_expression():
    r = client.post("/api/validate", json={
        "standard_id": STD,
        "entity_id": ENT,
        "source": SOURCE,
        "mappings": [{"source": "", "target": "class_uid",
                      "transform": "constant", "expression": ""}],
    })
    assert r.json()["valid"] is False
    assert any("needs an expression" in e for e in r.json()["errors"])


def test_defaults_reach_approval_and_export():
    mappings = _suggest() + _defaults(_suggest())
    r = client.post("/api/validate", json={
        "standard_id": STD,
        "entity_id": ENT,
        "source": SOURCE,
        "mappings": mappings,
    })
    assert r.json()["valid"] is True, r.json()["errors"]

    r = client.post("/api/approve", json={
        "standard_id": STD,
        "entity_id": ENT,
        "source": SOURCE,
        "mappings": mappings,
        "approved_by": "data-owner",
    })
    assert r.status_code == 200
    approval_id = r.json()["approval"]["id"]

    r = client.post("/api/export", json={
        "approval_id": approval_id,
        "target_catalog": "main",
        "target_schema": "curated",
        "output_table": "network_activity",
    })
    sql = next(f["content"] for f in r.json()["files"]
               if f["path"] == "transform.sql")
    assert "4001 AS class_uid" in sql
    assert "'1.9.0' AS metadata_version" in sql


def test_model_provenance_offline():
    assert client.get("/api/health").json()["model"] is None
    body = client.post("/api/suggest", json={
        "standard_id": STD, "entity_id": ENT, "source": SOURCE,
    }).json()
    assert body["generated_by"] == "offline-demo"
    assert body["model"] == "offline-heuristic"
    assert body["llm_error"] is None

    mappings = _complete_mappings()
    r = client.post("/api/approve", json={
        "standard_id": STD, "entity_id": ENT, "source": SOURCE,
        "mappings": mappings, "approved_by": "data-owner",
    })
    approval_id = r.json()["approval"]["id"]
    body = client.post("/api/export", json={
        "approval_id": approval_id, "target_catalog": "main",
        "target_schema": "curated", "output_table": "network_activity",
    }).json()
    assert body["generated_by"] == "template"
    assert body["model"] == "template"
    assert body["llm_error"] is None


def test_llm_failure_surfaces_reason(monkeypatch):
    import app.exporter as exporter
    import app.mapper as mapper
    from app.models import SourceSchema

    monkeypatch.setattr(exporter, "llm_enabled", lambda: True)
    monkeypatch.setattr(exporter, "chat_json", lambda *a, **k: None)
    monkeypatch.setattr(exporter, "last_error", lambda: "boom")
    result = exporter.export_artifacts([], [], "main", "curated", "t", "src")
    assert result["generated_by"] == "template"
    assert result["llm_error"] == "boom"

    monkeypatch.setattr(mapper, "llm_enabled", lambda: True)
    monkeypatch.setattr(mapper, "chat_json", lambda *a, **k: None)
    monkeypatch.setattr(mapper, "last_error", lambda: "boom")
    _, entity = registry.get_entity(STD, ENT)
    src = SourceSchema(name="s", fields=[{"name": "x"}])
    result = mapper.suggest_mappings(src, entity)
    assert result["generated_by"] == "offline-demo"
    assert result["llm_error"] == "boom"


def test_export_honors_entered_catalog():
    r = client.post("/api/approve", json={
        "standard_id": STD, "entity_id": ENT, "source": SOURCE,
        "mappings": _complete_mappings(), "approved_by": "data-owner",
    })
    aid = r.json()["approval"]["id"]
    body = client.post("/api/export", json={
        "approval_id": aid, "target_catalog": "MYCAT",
        "target_schema": "myschema", "output_table": "mytable",
    }).json()
    assert body["target"] == "MYCAT.myschema.mytable"
    py = next(f["content"] for f in body["files"] if f["path"] == "transform.py")
    assert 'TARGET_TABLE = "MYCAT.myschema.mytable"' in py
    sql = next(f["content"] for f in body["files"] if f["path"] == "transform.sql")
    assert "CREATE OR REPLACE TABLE MYCAT.myschema.mytable" in sql


def test_llm_artifacts_must_honor_target(monkeypatch):
    import app.exporter as exporter

    monkeypatch.setattr(exporter, "llm_enabled", lambda: True)
    good = {
        "transform_py": 'TARGET_TABLE = "MYCAT.myschema.mytable"',
        "transform_sql": "CREATE TABLE MYCAT.myschema.mytable AS SELECT 1",
        "dq_tests_py": "check mytable",
    }
    monkeypatch.setattr(exporter, "chat_json", lambda *a, **k: dict(good))
    result = exporter.export_artifacts([], [], "MYCAT", "myschema", "mytable", "src")
    assert result["generated_by"] == "meta"
    assert result["llm_error"] is None

    bad = {
        "transform_py": 'TARGET_TABLE = "main.default.events"',
        "transform_sql": "CREATE TABLE main.default.events AS SELECT 1",
        "dq_tests_py": "check events",
    }
    monkeypatch.setattr(exporter, "chat_json", lambda *a, **k: dict(bad))
    result = exporter.export_artifacts([], [], "MYCAT", "myschema", "mytable", "src")
    assert result["generated_by"] == "template"
    assert "MYCAT.myschema.mytable" in (result["llm_error"] or "")
    py = next(f["content"] for f in result["files"] if f["path"] == "transform.py")
    assert "MYCAT.myschema.mytable" in py


def test_chat_json_strips_fences(monkeypatch):
    import app.llm as llm

    monkeypatch.setattr(llm, "chat", lambda *a, **k: '```json\n{"a": 1}\n```')
    assert llm.chat_json("s", "u") == {"a": 1}


def test_hydrate_unknown_standard_404():
    r = client.post("/api/standards/hydrate", json={"standard_id": "nope"})
    assert r.status_code == 404


def test_hydrate_pending_activate_roundtrip(isolated_registry):
    r = client.post("/api/standards/hydrate",
                    json={"standard_id": "ocsf-unmanned-systems"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert r.json()["fetched_by"] == "mcp-direct"
    assert r.json()["model"] is None

    r = client.get("/api/standards-pending")
    assert "ocsf-unmanned-systems" in {p["id"] for p in r.json()["pending"]}

    # Not in the active registry until a human activates it.
    assert client.get("/api/standards/ocsf-unmanned-systems").status_code == 404

    r = client.post("/api/standards/ocsf-unmanned-systems/activate")
    assert r.status_code == 200
    assert r.json()["status"] == "active"

    detail = client.get("/api/standards/ocsf-unmanned-systems").json()
    assert detail["name"] == "OCSF Unmanned Systems"
    assert len(detail["entities"]) == 2
    assert all(e["fields"] for e in detail["entities"])

    # Pending queue is empty again after activation.
    assert client.get("/api/standards-pending").json()["pending"] == []


def test_hydrate_otel_semconv(isolated_registry):
    r = client.post("/api/standards/hydrate",
                    json={"standard_id": "otel-semconv"})
    assert r.status_code == 200, r.text
    assert r.json()["fetched_by"] == "mcp-direct"

    r = client.post("/api/standards/otel-semconv/activate")
    assert r.status_code == 200

    detail = client.get("/api/standards/otel-semconv").json()
    by_id = {e["id"]: e for e in detail["entities"]}
    assert {"log-record", "resource", "scope", "span"} <= set(by_id)
    assert "body" in {f["path"] for f in by_id["log-record"]["fields"]}
    assert "service.name" in {f["path"] for f in by_id["resource"]["fields"]}

    r = client.post("/api/suggest", json={
        "standard_id": "otel-semconv",
        "entity_id": "log-record",
        "source": {"name": "otel_logs", "fields": [
            {"name": "body"}, {"name": "severity_text"}, {"name": "trace_id"},
        ]},
    })
    targets = {m["source"]: m["target"] for m in r.json()["mappings"]}
    assert targets == {"body": "body", "severity_text": "severity_text",
                       "trace_id": "trace_id"}


def test_infer_csv_upload():
    csv_data = ("event_time,src_ip,src_port\n"
                "2024-01-02 03:04:05,10.0.0.1,443\n"
                "2024-01-02 03:04:06,10.0.0.2,80\n")
    r = client.post("/api/infer", files={"file": ("fw.csv", csv_data, "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    types = {f["name"]: f["type"] for f in body["schema"]["fields"]}
    assert types == {"event_time": "datetime", "src_ip": "string",
                     "src_port": "integer"}
    assert body["row_count"] == 2
    assert len(body["sample_rows"]) == 2


def test_infer_rejects_garbage():
    r = client.post("/api/infer", files={"file": ("x.json", "not json", "text/plain")})
    assert r.status_code == 400


def test_dryrun_endpoint():
    r = client.post("/api/dryrun", json={
        "standard_id": STD,
        "entity_id": ENT,
        "mappings": [
            {"source": "event_time", "target": "time", "transform": "cast"},
            {"source": "", "target": "class_uid", "transform": "constant",
             "expression": "4001"},
        ],
        "rows": [{"event_time": "2024-01-02 03:04:05"}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["time", "class_uid"]
    assert body["errors"] == 0
    assert body["rows"][0]["class_uid"] == 4001


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from app import projects as proj
    monkeypatch.setattr(proj, "DB_PATH", tmp_path / "foundry.db")


def test_projects_crud(tmp_db):
    payload = {"name": "fw", "standard_id": STD, "entity_id": ENT,
               "source": SOURCE, "mappings": []}
    r = client.post("/api/projects", json=payload)
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.get("/api/projects").json()["projects"][0]["name"] == "fw"
    payload["name"] = "fw2"
    assert client.put(f"/api/projects/{pid}", json=payload).json()["name"] == "fw2"
    assert client.get(f"/api/projects/{pid}").json()["id"] == pid
    assert client.delete(f"/api/projects/{pid}").json() == {"deleted": True}
    assert client.get("/api/projects").json()["projects"] == []
    assert client.get("/api/projects/9999").status_code == 404


def test_approvals_list_and_drift_clean():
    mappings = _complete_mappings()
    r = client.post("/api/approve", json={
        "standard_id": STD, "entity_id": ENT, "source": SOURCE,
        "mappings": mappings, "approved_by": "data-owner",
    })
    aid = r.json()["approval"]["id"]
    ids = [a["id"] for a in client.get("/api/approvals").json()["approvals"]]
    assert aid in ids
    assert client.get(f"/api/approvals/{aid}").json()["approved_by"] == "data-owner"
    drift = client.get(f"/api/approvals/{aid}/drift").json()
    assert drift["valid"] is True
    assert drift["missing_targets"] == []
    assert drift["registry_changed"] is False


def test_drift_detects_removed_target(tmp_path, monkeypatch):
    import json as _json

    reg_a = tmp_path / "a"
    reg_b = tmp_path / "b"
    reg_a.mkdir()
    reg_b.mkdir()
    for path in registry.DATA_DIR.glob("*.json"):
        shutil.copy(path, reg_a / path.name)
        shutil.copy(path, reg_b / path.name)
    net_b = reg_b / "ocsf-network.json"
    doc = _json.loads(net_b.read_text())
    for e in doc["entities"]:
        if e["id"] == ENT:
            e["fields"] = [f for f in e["fields"] if f["path"] != "action"]
    net_b.write_text(_json.dumps(doc))
    monkeypatch.setattr(registry, "DATA_DIR", reg_a)
    registry.refresh()
    try:
        mappings = _suggest()
        assert any(m["target"] == "action" for m in mappings)
        r = client.post("/api/approve", json={
            "standard_id": STD, "entity_id": ENT, "source": SOURCE,
            "mappings": _complete_mappings(), "approved_by": "data-owner",
        })
        assert r.status_code == 200, r.text
        aid = r.json()["approval"]["id"]
        monkeypatch.setattr(registry, "DATA_DIR", reg_b)
        registry.refresh()
        drift = client.get(f"/api/approvals/{aid}/drift").json()
        assert drift["registry_changed"] is True
        assert drift["missing_targets"] == ["action"]
    finally:
        registry.refresh()


def test_deploy_validation():
    mappings = _complete_mappings()
    r = client.post("/api/approve", json={
        "standard_id": STD, "entity_id": ENT, "source": SOURCE,
        "mappings": mappings, "approved_by": "data-owner",
    })
    aid = r.json()["approval"]["id"]
    assert client.post("/api/deploy", json={
        "approval_id": "APR-NOPE", "host": "https://x", "token": "t",
    }).status_code == 404
    r = client.post("/api/deploy", json={
        "approval_id": aid, "host": "https://x", "token": "",
        "run_sql": False,
    })
    assert r.status_code == 502


def test_deploy_mock_transport():
    import httpx
    from app import deployer

    calls = []
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        calls.append(request.url.path)
        bodies.append(_json.loads(request.content or b"{}"))
        if request.url.path.endswith("/schemas"):
            return httpx.Response(400, json={
                "error_code": "SCHEMA_ALREADY_EXISTS",
                "message": "Schema 'curated' already exists",
            })
        if request.url.path.endswith("/statements"):
            return httpx.Response(200, json={"statement_id": "s1",
                                             "status": {"state": "SUCCEEDED"}})
        return httpx.Response(200, json={})

    client_ = httpx.Client(transport=httpx.MockTransport(handler),
                           base_url="https://x")
    result = deployer.deploy(
        "https://x", "tok", "wh", "main", "curated", "/TF/t",
        [{"path": "transform.py", "content": "x = 1"},
         {"path": "databricks.yml", "content": "bundle: {}"}],
        "SELECT 1", run_sql=True, client=client_)
    assert result["imported"] == ["/TF/t/transform.py",
                                  "/TF/t/databricks.yml"]
    assert result["schema"] == {"already_exists": True}
    assert result["statement"]["status"] == "SUCCEEDED"
    assert any(p.endswith("/schemas") for p in calls)
    assert any(p.endswith("/mkdirs") for p in calls)
    imports = [b for b in bodies if "content" in b]
    py = next(b for b in imports if b["path"].endswith(".py"))
    assert py["format"] == "SOURCE" and py["language"] == "PYTHON"
    yml = next(b for b in imports if b["path"].endswith(".yml"))
    assert yml["format"] == "AUTO" and "language" not in yml


def test_deploy_translates_databricks_errors():
    import httpx
    from app import deployer

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={
            "error_code": "CATALOG_DOES_NOT_EXIST",
            "message": "Catalog 'main' does not exist.",
        })

    client_ = httpx.Client(transport=httpx.MockTransport(handler),
                           base_url="https://x")
    try:
        deployer.deploy("https://x", "tok", "wh", "main", "curated",
                        "/TF/t", [], "SELECT 1", run_sql=False,
                        client=client_)
        raise AssertionError("expected DeployError")
    except deployer.DeployError as exc:
        assert "CATALOG_DOES_NOT_EXIST" in str(exc)
        assert "Catalog name in the export form" in str(exc)


def test_export_includes_dlt():
    mappings = _complete_mappings()
    r = client.post("/api/approve", json={
        "standard_id": STD, "entity_id": ENT, "source": SOURCE,
        "mappings": mappings, "approved_by": "data-owner",
    })
    aid = r.json()["approval"]["id"]
    body = client.post("/api/export", json={
        "approval_id": aid, "target_catalog": "main",
        "target_schema": "curated", "output_table": "network_activity",
    }).json()
    dlt = next(f["content"] for f in body["files"]
               if f["path"] == "dlt_pipeline.py")
    assert "@dlt.table" in dlt
    assert "expect_all_or_drop" in dlt
    assert "CAST(`event_time` AS TIMESTAMP)" in dlt


def test_agent_onboard_offline():
    r = client.post("/api/agent/onboard", json={
        "source": SOURCE,
        "rows": [{"event_time": "2024-01-02 03:04:05", "src_ip": "10.0.0.1"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["standard_id"] == "ocsf-network"
    assert body["entity_id"] == "network_activity"
    assert body["validation"]["valid"] is True
    assert body["classification"]["generated_by"] == "offline-demo"
    agents = [s["agent"] for s in body["trace"]]
    assert agents[:4] == ["classifier", "mapper", "defaults", "validator"]
    assert body["dry_run"]["columns"]
    assert r.json()["mappings"]


def test_agent_onboard_rejects_empty_source():
    r = client.post("/api/agent/onboard", json={
        "source": {"name": "empty", "fields": []}})
    assert r.status_code == 400


def test_agent_refine_fixes_duplicates_offline():
    mappings = [
        {"source": "event_time", "target": "time"},
        {"source": "src_ip", "target": "time"},
    ]
    r = client.post("/api/agent/refine", json={
        "standard_id": STD, "entity_id": ENT, "source": SOURCE,
        "mappings": mappings,
    })
    body = r.json()
    assert body["validation"]["valid"] is True
    targets = [m["target"] for m in body["mappings"] if m["target"]]
    assert len(targets) == len(set(targets))
    assert body["rounds"] >= 1
    assert any(s["agent"] == "critic" for s in body["trace"])


def test_agent_classify_llm_path(monkeypatch):
    import app.agents as agents

    monkeypatch.setattr(agents, "llm_enabled", lambda: True)
    monkeypatch.setattr(agents, "chat_json", lambda *a, **k: {
        "standard_id": STD, "entity_id": ENT, "confidence": 0.9,
        "rationale": "test",
    })
    body = client.post("/api/agent/onboard", json={"source": SOURCE}).json()
    assert body["classification"]["generated_by"] == "meta"
    assert body["classification"]["entity_id"] == ENT


def test_agent_refine_llm_path(monkeypatch):
    import app.agents as agents

    fixed = _complete_mappings()
    monkeypatch.setattr(agents, "llm_enabled", lambda: True)
    monkeypatch.setattr(agents, "chat_json", lambda *a, **k: {"mappings": fixed})
    r = client.post("/api/agent/refine", json={
        "standard_id": STD, "entity_id": ENT, "source": SOURCE,
        "mappings": [{"source": "event_time", "target": "time"}],
    })
    body = r.json()
    assert body["validation"]["valid"] is True
    assert body["rounds"] == 2
    critic_steps = [s for s in body["trace"] if s["agent"] == "critic"]
    assert critic_steps and critic_steps[0]["detail"].startswith("meta")
