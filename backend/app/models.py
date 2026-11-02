"""Pydantic contracts for mapping, validation, approval, and export."""

from typing import Any, Optional
from pydantic import BaseModel, Field


class SourceField(BaseModel):
    name: str
    type: str = "string"
    sample: Optional[Any] = None


class SourceSchema(BaseModel):
    name: str = "source"
    fields: list[SourceField] = Field(default_factory=list)


class MappingEntry(BaseModel):
    source: str
    target: str
    transform: str = "direct"
    expression: Optional[str] = None
    confidence: float = 1.0
    rationale: Optional[str] = None


class SuggestRequest(BaseModel):
    standard_id: str
    entity_id: Optional[str] = None
    source: SourceSchema


class ValidateRequest(BaseModel):
    standard_id: str
    entity_id: Optional[str] = None
    source: SourceSchema
    mappings: list[MappingEntry] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    standard_id: str
    entity_id: Optional[str] = None
    source: SourceSchema
    mappings: list[MappingEntry] = Field(default_factory=list)
    approved_by: str = "reviewer"
    notes: Optional[str] = None


class ExportRequest(BaseModel):
    approval_id: str
    target_catalog: str = "main"
    target_schema: str = "curated"
    output_table: Optional[str] = None
    source_table: str = "bronze.source_records"


class StandardField(BaseModel):
    path: str
    type: str = "string"
    required: bool = False
    description: str = ""


class StandardEntity(BaseModel):
    id: str
    name: str
    fields: list[StandardField] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class StandardDefinition(BaseModel):
    id: str
    name: str
    category: str = "data"
    version: str = ""
    description: str = ""
    entities: list[StandardEntity] = Field(default_factory=list)


class HydrateRequest(BaseModel):
    standard_id: str


class ProjectRequest(BaseModel):
    name: str
    standard_id: str
    entity_id: Optional[str] = None
    source: SourceSchema
    mappings: list[MappingEntry] = Field(default_factory=list)


class DryRunRequest(BaseModel):
    standard_id: str
    entity_id: Optional[str] = None
    mappings: list[MappingEntry] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class RefineRequest(BaseModel):
    standard_id: str
    entity_id: Optional[str] = None
    source: SourceSchema
    mappings: list[MappingEntry] = Field(default_factory=list)
    max_rounds: int = 3


class OnboardRequest(BaseModel):
    source: SourceSchema
    rows: list[dict[str, Any]] = Field(default_factory=list)
    max_rounds: int = 3


class DeployRequest(BaseModel):
    approval_id: str
    host: str
    token: str
    warehouse_id: str = ""
    target_catalog: str = "main"
    target_schema: str = "curated"
    output_table: Optional[str] = None
    source_table: str = "bronze.source_records"
    workspace_dir: Optional[str] = None
    run_sql: bool = True
