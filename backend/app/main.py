"""FastAPI entrypoint: standards, suggest, validate, approve, export."""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .agents import onboard_source, refine_mappings
from .approvals import create_approval, get_approval, list_approvals
from .defaults import suggest_defaults
from .deployer import DeployError, deploy
from .evaluator import dry_run as run_dryrun
from .exporter import _col, export_artifacts, spark_type
from . import hydrator
from .hydrator import UnknownStandardError
from .inference import infer_schema
from .llm import llm_enabled, model_name
from .mapper import suggest_mappings
from .models import (
    ApproveRequest,
    DeployRequest,
    DryRunRequest,
    ExportRequest,
    HydrateRequest,
    OnboardRequest,
    ProjectRequest,
    RefineRequest,
    SuggestRequest,
    ValidateRequest,
)
from . import projects
from .standards import fingerprint, get_entity, get_standard, list_standards
from .validator import validate_mappings

app = FastAPI(title="Telemetry Foundry")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "mode": "meta" if llm_enabled() else "demo",
            "model": model_name() if llm_enabled() else None}


@app.get("/api/standards")
def standards() -> dict:
    return {"standards": list_standards()}


@app.get("/api/standards/{standard_id}")
def standard_detail(standard_id: str) -> dict:
    standard = get_standard(standard_id)
    if not standard:
        raise HTTPException(status_code=404, detail="Unknown standard")
    return standard


@app.get("/api/standards-pending")
def pending_standards() -> dict:
    return {"pending": hydrator.list_pending()}


@app.post("/api/standards/hydrate")
def hydrate_standard(req: HydrateRequest) -> dict:
    try:
        return hydrator.hydrate(req.standard_id)
    except UnknownStandardError:
        raise HTTPException(status_code=404, detail="Unknown standard")


@app.post("/api/standards/{standard_id}/activate")
def activate_standard(standard_id: str) -> dict:
    try:
        return hydrator.activate(standard_id)
    except UnknownStandardError:
        raise HTTPException(status_code=404, detail="No pending standard") from None


@app.post("/api/suggest")
def suggest(req: SuggestRequest) -> dict:
    _, entity = get_entity(req.standard_id, req.entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Unknown standard or entity")
    result = suggest_mappings(req.source, entity)
    return {
        "mappings": [m.model_dump() for m in result["mappings"]],
        "generated_by": result["generated_by"],
        "model": result["model"],
        "llm_error": result["llm_error"],
    }


@app.post("/api/validate")
def validate(req: ValidateRequest) -> dict:
    _, entity = get_entity(req.standard_id, req.entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Unknown standard or entity")
    return validate_mappings(req.source, entity, req.mappings)


@app.post("/api/defaults")
def defaults(req: ValidateRequest) -> dict:
    standard, entity = get_entity(req.standard_id, req.entity_id)
    if not standard or not entity:
        raise HTTPException(status_code=404, detail="Unknown standard or entity")
    added = suggest_defaults(standard, entity, req.mappings)
    return {"added": [m.model_dump() for m in added]}


@app.post("/api/approve")
def approve(req: ApproveRequest) -> dict:
    _, entity = get_entity(req.standard_id, req.entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Unknown standard or entity")
    report = validate_mappings(req.source, entity, req.mappings)
    if not report["valid"]:
        raise HTTPException(
            status_code=422,
            detail={"message": "Mappings have errors", "report": report},
        )
    record = create_approval({
        "standard_id": req.standard_id,
        "entity_id": entity.get("id"),
        "source": req.source.model_dump(),
        "mappings": [m.model_dump() for m in req.mappings],
        "approved_by": req.approved_by,
        "notes": req.notes,
        "validation": report,
        "registry_fingerprint": fingerprint(),
    })
    return {"approval": record}


def _artifacts_for(record: dict, catalog: str, schema: str,
                   table: Optional[str], source_table: str) -> tuple[dict, str]:
    from .models import MappingEntry as ME

    _, entity = get_entity(record["standard_id"], record.get("entity_id"))
    if not entity:
        raise HTTPException(status_code=404, detail="Standard no longer available")
    mappings = [ME(**m) for m in record["mappings"]]
    required = [
        _col(f["path"]) for f in entity.get("fields", []) if f.get("required")
    ]
    target_types = {
        f["path"]: spark_type(f.get("type")) for f in entity.get("fields", [])
    }
    table = table or f"{record['standard_id']}_{entity.get('id')}"
    result = export_artifacts(
        mappings, required, catalog, schema, table, source_table, target_types,
    )
    return result, f"{catalog}.{schema}.{table}"


@app.post("/api/export")
def export(req: ExportRequest) -> dict:
    record = get_approval(req.approval_id)
    if not record:
        raise HTTPException(status_code=404, detail="Unknown approval id")
    result, target = _artifacts_for(
        record, req.target_catalog, req.target_schema,
        req.output_table, req.source_table,
    )
    return {
        "approval_id": req.approval_id,
        "target": target,
        "generated_by": result["generated_by"],
        "model": result["model"],
        "llm_error": result["llm_error"],
        "files": result["files"],
    }


@app.post("/api/infer")
async def infer(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    try:
        return infer_schema(file.filename or "upload", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/dryrun")
def dryrun(req: DryRunRequest) -> dict:
    _, entity = get_entity(req.standard_id, req.entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Unknown standard or entity")
    target_types = {
        f["path"]: spark_type(f.get("type")) for f in entity.get("fields", [])
    }
    return run_dryrun([x.model_dump() for x in req.mappings],
                      req.rows[:200], target_types)


@app.get("/api/projects")
def projects_list() -> dict:
    return {"projects": projects.list_projects()}


@app.post("/api/projects")
def projects_create(req: ProjectRequest) -> dict:
    _, entity = get_entity(req.standard_id, req.entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Unknown standard or entity")
    return projects.create_project(
        req.name, req.standard_id, entity.get("id"),
        req.source.model_dump(), [m.model_dump() for m in req.mappings])


@app.get("/api/projects/{project_id}")
def projects_get(project_id: int) -> dict:
    project = projects.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Unknown project")
    return project


@app.put("/api/projects/{project_id}")
def projects_update(project_id: int, req: ProjectRequest) -> dict:
    _, entity = get_entity(req.standard_id, req.entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Unknown standard or entity")
    project = projects.update_project(
        project_id, req.name, req.standard_id, entity.get("id"),
        req.source.model_dump(), [m.model_dump() for m in req.mappings])
    if not project:
        raise HTTPException(status_code=404, detail="Unknown project")
    return project


@app.delete("/api/projects/{project_id}")
def projects_delete(project_id: int) -> dict:
    if not projects.delete_project(project_id):
        raise HTTPException(status_code=404, detail="Unknown project")
    return {"deleted": True}


@app.get("/api/approvals")
def approvals_list() -> dict:
    return {"approvals": list_approvals()}


@app.get("/api/approvals/{approval_id}")
def approvals_get(approval_id: str) -> dict:
    record = get_approval(approval_id)
    if not record:
        raise HTTPException(status_code=404, detail="Unknown approval id")
    return record


@app.get("/api/approvals/{approval_id}/drift")
def approvals_drift(approval_id: str) -> dict:
    from .models import MappingEntry as ME
    from .models import SourceSchema as SS

    record = get_approval(approval_id)
    if not record:
        raise HTTPException(status_code=404, detail="Unknown approval id")
    _, entity = get_entity(record["standard_id"], record.get("entity_id"))
    if not entity:
        return {"approval_id": approval_id, "registry_changed": True,
                "valid": False, "errors": ["Standard or entity no longer exists."],
                "warnings": [], "missing_targets": []}
    mappings = [ME(**m) for m in record["mappings"]]
    report = validate_mappings(SS(**record["source"]), entity, mappings)
    current_paths = {f.get("path") for f in entity.get("fields", [])}
    missing = sorted({m.target for m in mappings
                      if m.target and m.target not in current_paths})
    return {"approval_id": approval_id,
            "registry_changed": record.get("registry_fingerprint") != fingerprint(),
            "valid": report["valid"], "errors": report["errors"],
            "warnings": report["warnings"], "missing_targets": missing}


@app.post("/api/agent/refine")
def agent_refine(req: RefineRequest) -> dict:
    try:
        return refine_mappings(req.standard_id, req.entity_id, req.source,
                               req.mappings, req.max_rounds)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/agent/onboard")
def agent_onboard(req: OnboardRequest) -> dict:
    if not req.source.fields:
        raise HTTPException(status_code=400, detail="Source has no fields")
    try:
        return onboard_source(req.source, req.rows, req.max_rounds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/deploy")
def deploy_bundle(req: DeployRequest) -> dict:
    record = get_approval(req.approval_id)
    if not record:
        raise HTTPException(status_code=404, detail="Unknown approval id")
    result, target = _artifacts_for(
        record, req.target_catalog, req.target_schema,
        req.output_table, req.source_table,
    )
    main_sql = next(f["content"] for f in result["files"]
                    if f["path"] == "transform.sql")
    table_slug = target.rsplit(".", 1)[-1]
    workspace_dir = req.workspace_dir or f"/TelemetryFoundry/{table_slug}"
    try:
        deployment = deploy(
            req.host, req.token, req.warehouse_id, req.target_catalog,
            req.target_schema, workspace_dir, result["files"], main_sql,
            run_sql=req.run_sql,
        )
    except DeployError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"approval_id": req.approval_id, "target": target,
            "workspace_dir": workspace_dir, "deployment": deployment}
