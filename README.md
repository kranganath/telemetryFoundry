# Telemetry Foundry

AI-assisted tool that maps current logging schemas to **OCSF** (Open
Cybersecurity Schema Framework) as the unified security data warehouse,
validates the mappings, and — after data-owner approval — exports
Databricks-ready artifacts (PySpark, Spark SQL, data-quality tests, and a
Databricks Asset Bundle) with the required type conversions baked in.

- **Demo mode works offline.** Set Meta API credentials to get
  LLM-structured mapping suggestions, agent-normalized hydration, and
  LLM-generated pipeline code.
- **OCSF snapshot, all classes.** The registry holds all 61 active OCSF 1.9.0
  event classes (27k+ attributes) across 7 categories, converted from
  schema.ocsf.io. The 8th category (Unmanned Systems) arrives via MCP
  hydration.

## Quickstart

Backend (FastAPI, port 8000):

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
python -m uvicorn app.main:app --port 8000
```

Frontend (React + Vite, port 5173):

```sh
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 in a browser.

## Optional: Meta model API

Copy `backend/.env.example` to `backend/.env` and fill it in — the backend
loads it automatically on startup — or export the same variables in your
shell (shell variables take precedence over the file):

```sh
export META_API_BASE_URL="https://api.llama.com/v1"  # or an internal endpoint
export META_API_KEY="<key>"
export META_MODEL="Llama-4-Maverick-17B-128E-Instruct-FP8"
```

The header badge switches from "Demo mode (offline)" to "Meta API".
Without these variables every endpoint falls back to templates — nothing fails.

## Scaling to new standards: hydration pipeline

Fetching standards is a **hydration pipeline**, not a mapping-time agent call:
mapping requests stay fast, deterministic, and auditable against a versioned
local registry, while an MCP-using agent does the fetching once per standard:

```
MCP server (list_standards / get_standard tools)
        |
        v
hydrate -> validate against schema -> data/standards_pending/
        |
        v  human activate
data/standards/*.json (active registry, served to mapping)
```

- Without the Meta API: direct MCP fetch + schema validation (`mcp-direct`).
- With the Meta API: a tool-use agent loop may list/fetch via MCP and must
  return the definition normalized to the registry schema (`mcp-agent`),
  falling back to direct fetch on any failure.
- The repo ships a demo MCP server (`backend/mcp_server/`) serving
  `ocsf-unmanned-systems` and `otel-semconv` (OpenTelemetry log / resource /
  scope / span conventions). Point `STANDARDS_MCP_COMMAND` at any real MCP
  server exposing the same two tools to hydrate from your own sources.

## Flow

1. Pick a target OCSF category and event class.
2. Describe the current logging schema: paste CSV columns, keep a sample
   extract, or **upload a CSV/JSON log file** to infer columns, types, and
   sample rows.
3. Review AI mapping suggestions; type mismatches are flagged as `cast`
   conversions. Use **Auto-fill missing** to cover required metadata columns
   absent from the extract (real OCSF UIDs where known, placeholders
   otherwise) — every auto-fill is marked for review. **Dry-run** the
   mappings on sample rows to preview output before validating.
4. Validate (required-target coverage, duplicates, type warnings).
5. Data-owner approval — export stays locked until this passes.
6. Export: `transform.py`, `transform.sql` (with `CAST`s), `dq_tests.py`,
   `dlt_pipeline.py`, `databricks.yml` — then **deploy** straight to your
   workspace (schema + files + SQL execution).

Side views: **Projects** saves mapping drafts to resume later; **History**
lists every approval with one-click drift re-checks against the current
registry.

Deploy the bundle with `databricks bundle deploy` (Databricks CLI), or run
`transform.py` / `transform.sql` directly on your workspace.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status + `demo` / `meta` mode |
| GET | `/api/standards` | Registry listing |
| GET | `/api/standards/{id}` | Full standard definition |
| POST | `/api/suggest` | Mapping suggestions |
| POST | `/api/validate` | Validation report |
| POST | `/api/defaults` | Constant suggestions for unmapped required targets |
| POST | `/api/infer` | Infer source schema from an uploaded CSV/JSON file |
| POST | `/api/dryrun` | Preview transformed output for sample rows |
| GET/POST | `/api/projects` | Saved mapping drafts |
| GET/PUT/DELETE | `/api/projects/{id}` | One draft |
| GET | `/api/approvals` | Approval audit list |
| GET | `/api/approvals/{id}` | One approval record |
| GET | `/api/approvals/{id}/drift` | Re-validate against the current registry |
| POST | `/api/deploy` | Deploy bundle to Databricks (schema, files, SQL) |
| POST | `/api/agent/refine` | Validate-and-fix loop (critic) over mappings |
| POST | `/api/agent/onboard` | Full proposal: classify, map, fill, refine, dry-run |
| POST | `/api/approve` | Human approval (requires valid mappings) |
| POST | `/api/export` | Databricks artifacts for an approval |
| POST | `/api/standards/hydrate` | Fetch a standard via MCP into pending |
| GET | `/api/standards-pending` | Pending (unactivated) standards |
| POST | `/api/standards/{id}/activate` | Human activation into the registry |

## Layout

```
backend/
  app/            FastAPI app (models, standards, mapper, validator,
                  approvals, exporter, llm, mcp_client, hydrator,
                  defaults, evaluator, inference, projects, deployer)
  data/standards/ OCSF category definitions (JSON snapshot)
  data/foundry.db Saved projects (SQLite, created on first use)
  mcp_server/     Demo MCP server + sources for hydration
  scripts/        OCSF snapshot importer (fetch_ocsf.py)
  tests/          API + evaluator tests (offline mode)
frontend/
  src/            React UI (standard → source → map → validate → approve → export)
```

## Transforms and expression syntax

| Transform | Meaning | Expression |
|---|---|---|
| `direct` | Copy the source column | ignored |
| `cast` | Convert to the OCSF target type | optional override; empty auto-generates `CAST(\`col\` AS TIMESTAMP)` etc. |
| `expression` | Full Spark SQL expression | required, e.g. `concat(\`a\`, '-', \`b\`)` |
| `constant` | Fixed value (no source column) | required, e.g. `'firewall'`, `4001`, `current_timestamp()` |

The UI previews the generated `CAST` under each cast row. Auto-filled
constants land as `constant` mappings and flow unchanged into `transform.py`,
`transform.sql`, and the data-quality checks.

## Agentic layer

Specialists do one job — **classifier** (picks the OCSF class, heuristic or
LLM), **mapper**, **defaults**, **critic** (repairs validation errors, then
re-validates, up to 3 rounds), **dry-run** — and the onboarding supervisor
chains them into a complete proposal from a raw source. Every run returns a
step-by-step **trace** shown in the UI.

Agents propose, owners dispose: approval and deployment stay manual. Without
LLM credentials the same pipeline runs on offline heuristics with
deterministic critic repairs.

## Upgrading the OCSF snapshot

Re-run the importer (pinned to `OCSF_VERSION` in the script, stdlib only):

```sh
cd backend
.venv/bin/python scripts/fetch_ocsf.py
```

It pulls every non-deprecated event class from schema.ocsf.io, flattens one
object level into dotted paths, keeps row-level `required` flags, and holds
back the smallest category for MCP hydration.

## Adding a custom standard

Drop a JSON file into `backend/data/standards/`:

```json
{
  "id": "my-standard",
  "name": "My Standard 1.0",
  "category": "data",
  "version": "1.0",
  "description": "...",
  "entities": [
    {"id": "record", "name": "Record", "fields": [
      {"path": "id", "type": "string", "required": true, "description": "..."}
    ]}
  ]
}
```

Restart the backend; the new standard appears in the UI with samples optional.
