import React, { useEffect, useState } from 'react'
import { api } from './api.js'

const SAMPLES = {
  'ocsf-network': {
    name: 'pan_traffic_logs',
    columns: 'receive_time,src_zone,dst_zone,src_ip,dst_ip,src_port,dst_port,connection_protocol,action,app,rule,bytes_sent,bytes_received,packets_sent,packets_received,session_key,start_time',
  },
  'ocsf-iam': {
    name: 'okta_system_log',
    columns: 'event_time,event_type,user_uid,user_email,client_ip,user_agent,outcome_result,service_name,session_uid,mfa_factor',
  },
  'ocsf-system': {
    name: 'edr_process_events',
    columns: 'event_time,hostname,process_uid,pid,process_path,process_name,command_line,user,parent_process_id,process_file,process_integrity',
  },
  'ocsf-findings': {
    name: 'tenable_findings',
    columns: 'finding_uid,finding_title,severity,host_ip,endpoint_hostname,host_os,mac_address,first_seen,last_seen,status,cve_list',
  },
  'ocsf-discovery': {
    name: 'edr_host_inventory',
    columns: 'hostname,domain,os,model,ip_address,mac_address,last_seen,agent_version,org_unit',
  },
  'ocsf-application': {
    name: 'nginx_access_logs',
    columns: 'client_ip,timestamp,http_method,request_uri,status_code,bytes_sent,referrer,user_agent,request_time',
  },
  'ocsf-remediation': {
    name: 'edr_response_actions',
    columns: 'action_name,status,hostname,category_name,start_time,end_time,action_id,action_status',
  },
  'ocsf-unmanned-systems': {
    name: 'adsb_flights',
    columns: 'serial_number,model,track_direction,lat,longitude,altitude_geodetic,ground_speed,timestamp,operator_uid',
  },
  'otel-semconv': {
    name: 'otel_logs',
    columns: 'timestamp,observed_timestamp,severity_text,body,trace_id,span_id,service_name,scope_name,host_name',
  },
}

const STEP_NAMES = ['Standard', 'Source', 'Mappings', 'Validate', 'Approve', 'Export']

const EXPR_HINT = {
  direct: '',
  cast: 'optional override, e.g. upper(`col`)',
  expression: 'e.g. concat(`a`, \'-\', `b`)',
  constant: "e.g. 'firewall', 4001, current_timestamp()",
}

const SPARK_TYPES = {
  datetime: 'TIMESTAMP', timestamp: 'TIMESTAMP', date: 'DATE',
  integer: 'BIGINT', number: 'BIGINT', decimal: 'DOUBLE', boolean: 'BOOLEAN',
}

function download(path, content) {
  const blob = new Blob([content], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = path
  a.click()
  URL.revokeObjectURL(url)
}

function Foot({ back, backLabel = 'Back', children }) {
  return (
    <div className="foot">
      {back
        ? <button className="btn ghost" onClick={back}>{backLabel}</button>
        : <span />}
      <div className="foot-actions">{children}</div>
    </div>
  )
}

export default function App() {
  const [mode, setMode] = useState('…')
  const [llmModel, setLlmModel] = useState('')
  const [step, setStep] = useState(0)
  const [standards, setStandards] = useState([])
  const [standardId, setStandardId] = useState('')
  const [detail, setDetail] = useState(null)
  const [entityId, setEntityId] = useState('')
  const [sourceName, setSourceName] = useState('source')
  const [columns, setColumns] = useState('')
  const [mappings, setMappings] = useState([])
  const [suggestBy, setSuggestBy] = useState('')
  const [suggestModel, setSuggestModel] = useState('')
  const [suggestLlmError, setSuggestLlmError] = useState('')
  const [report, setReport] = useState(null)
  const [approval, setApproval] = useState(null)
  const [approvedBy, setApprovedBy] = useState('data-owner')
  const [notes, setNotes] = useState('')
  const [catalog, setCatalog] = useState('main')
  const [schema, setSchema] = useState('curated')
  const [table, setTable] = useState('')
  const [sourceTable, setSourceTable] = useState('bronze.source_records')
  const [exportResult, setExportResult] = useState(null)
  const [activeFile, setActiveFile] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState('flow')
  const [fieldTypes, setFieldTypes] = useState({})
  const [fieldSamples, setFieldSamples] = useState({})
  const [sampleRows, setSampleRows] = useState([])
  const [inferInfo, setInferInfo] = useState('')
  const [dryRowsText, setDryRowsText] = useState('')
  const [dryResult, setDryResult] = useState(null)
  const [agentTrace, setAgentTrace] = useState([])
  const [onboardInfo, setOnboardInfo] = useState('')
  const [projects, setProjects] = useState([])
  const [projectName, setProjectName] = useState('')
  const [approvals, setApprovals] = useState([])
  const [approvalDetail, setApprovalDetail] = useState(null)
  const [drift, setDrift] = useState(null)
  const [depHost, setDepHost] = useState('')
  const [depToken, setDepToken] = useState('')
  const [depWarehouse, setDepWarehouse] = useState('')
  const [depRunSql, setDepRunSql] = useState(true)
  const [deployResult, setDeployResult] = useState(null)
  const [pending, setPending] = useState([])
  const [hydrateId, setHydrateId] = useState('otel-semconv')

  function refreshRegistry() {
    api.standards().then((s) => setStandards(s.standards)).catch((e) => setError(String(e)))
    api.pending().then((p) => setPending(p.pending)).catch(() => {})
  }

  useEffect(() => {
    api.health().then((h) => { setMode(h.mode); setLlmModel(h.model || '') }).catch(() => setMode('backend offline'))
    refreshRegistry()
  }, [])

  const entity = detail?.entities?.find((e) => e.id === entityId) || detail?.entities?.[0]

  async function pickStandard(id) {
    setError('')
    setStandardId(id)
    const d = await api.standard(id)
    setDetail(d)
    setEntityId(d.entities[0]?.id || '')
    const sample = SAMPLES[id]
    if (sample) {
      setSourceName(sample.name)
      setColumns(sample.columns)
      setFieldTypes({})
      setFieldSamples({})
      setSampleRows([])
      setInferInfo('')
    }
    setMappings([])
    setSuggestBy('')
    setSuggestModel('')
    setSuggestLlmError('')
    setAgentTrace([])
    setOnboardInfo('')
    setReport(null)
    setApproval(null)
    setExportResult(null)
  }

  function changeEntity(id) {
    if (id === entityId) return
    setEntityId(id)
    // Targets belong to the entity: clear everything derived from them so
    // the mapping screen can only show the new entity's columns.
    setMappings([])
    setSuggestBy('')
    setSuggestModel('')
    setSuggestLlmError('')
    setAgentTrace([])
    setOnboardInfo('')
    setReport(null)
    setApproval(null)
    setExportResult(null)
  }

  async function runHydrate() {
    setBusy(true)
    setError('')
    try {
      await api.hydrate(hydrateId.trim())
      refreshRegistry()
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function runActivate(id) {
    setBusy(true)
    setError('')
    try {
      await api.activate(id)
      refreshRegistry()
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  function sourcePayload() {
    return {
      name: sourceName || 'source',
      fields: columns.split(',').map((c) => c.trim()).filter(Boolean).map((name) => ({
        name,
        ...(fieldTypes[name] ? { type: fieldTypes[name] } : {}),
        ...(fieldSamples[name] !== undefined && fieldSamples[name] !== '' ? { sample: fieldSamples[name] } : {}),
      })),
    }
  }

  async function runSuggest() {
    setBusy(true)
    setError('')
    try {
      const r = await api.suggest({ standard_id: standardId, entity_id: entityId, source: sourcePayload() })
      setMappings(r.mappings)
      setSuggestBy(r.generated_by)
      setSuggestModel(r.model || '')
      setSuggestLlmError(r.llm_error || '')
      setAgentTrace([])
      setOnboardInfo('')
      setStep(2)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function runFillDefaults() {
    setBusy(true)
    setError('')
    try {
      const r = await api.defaults({
        standard_id: standardId, entity_id: entityId,
        source: sourcePayload(), mappings,
      })
      setMappings((ms) => [...ms, ...r.added])
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function runInfer(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true)
    setError('')
    try {
      const r = await api.infer(file)
      setSourceName(r.schema.name)
      setColumns(r.schema.fields.map((f) => f.name).join(','))
      const types = {}
      const samples = {}
      r.schema.fields.forEach((f) => {
        types[f.name] = f.type
        if (f.sample) samples[f.name] = f.sample
      })
      setFieldTypes(types)
      setFieldSamples(samples)
      setSampleRows(r.sample_rows || [])
      setInferInfo(`${file.name}: ${r.row_count} rows${r.truncated ? ' (scanned first 2000)' : ''}`)
      setMappings([])
      setReport(null)
      setApproval(null)
      setExportResult(null)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
      e.target.value = ''
    }
  }

  async function runDryRun(useSample) {
    setBusy(true)
    setError('')
    try {
      let rows = []
      if (useSample) {
        rows = sampleRows
      } else {
        rows = JSON.parse(dryRowsText || '[]')
        if (!Array.isArray(rows)) throw new Error('Rows must be a JSON array')
      }
      const r = await api.dryrun({
        standard_id: standardId, entity_id: entityId, mappings, rows,
      })
      setDryResult(r)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function openView(v) {
    setView(v)
    setError('')
    if (v === 'flow') return
    setBusy(true)
    try {
      if (v === 'projects') setProjects((await api.projects()).projects)
      if (v === 'history') {
        setApprovals((await api.approvals()).approvals)
        setApprovalDetail(null)
        setDrift(null)
      }
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function saveProject() {
    if (!projectName.trim()) {
      setError('Name the project first.')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.createProject({
        name: projectName.trim(), standard_id: standardId,
        entity_id: entityId, source: sourcePayload(), mappings,
      })
      setProjectName('')
      setProjects((await api.projects()).projects)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function overwriteProject(id, name) {
    if (!window.confirm(`Overwrite "${name}" with the current state?`)) return
    setBusy(true)
    setError('')
    try {
      await api.updateProject(id, {
        name, standard_id: standardId, entity_id: entityId,
        source: sourcePayload(), mappings,
      })
      setProjects((await api.projects()).projects)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function loadProject(id) {
    setBusy(true)
    setError('')
    try {
      const p = projects.find((x) => x.id === id)
      if (!p) throw new Error('Project not in list — reopen Projects.')
      const d = await api.standard(p.standard_id)
      setDetail(d)
      setStandardId(p.standard_id)
      setEntityId(p.entity_id)
      setSourceName(p.source.name || 'source')
      setColumns(p.source.fields.map((f) => f.name).join(','))
      const types = {}
      const samples = {}
      p.source.fields.forEach((f) => {
        if (f.type) types[f.name] = f.type
        if (f.sample) samples[f.name] = f.sample
      })
      setFieldTypes(types)
      setFieldSamples(samples)
      setSampleRows([])
      setMappings(p.mappings)
      setSuggestBy('')
      setSuggestModel('')
      setSuggestLlmError('')
      setReport(null)
      setApproval(null)
      setExportResult(null)
      setDryResult(null)
      setView('flow')
      setStep(2)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function removeProject(id, name) {
    if (!window.confirm(`Delete project "${name}"?`)) return
    setBusy(true)
    setError('')
    try {
      await api.deleteProject(id)
      setProjects((await api.projects()).projects)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function openApproval(id) {
    setBusy(true)
    setError('')
    try {
      setApprovalDetail(await api.approval(id))
      setDrift(null)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function checkDrift(id) {
    setBusy(true)
    setError('')
    try {
      setDrift(await api.drift(id))
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function runDeploy() {
    setBusy(true)
    setError('')
    try {
      const r = await api.deploy({
        approval_id: approval.id, host: depHost, token: depToken,
        warehouse_id: depWarehouse, target_catalog: catalog,
        target_schema: schema, output_table: table || undefined,
        source_table: sourceTable, run_sql: depRunSql,
      })
      setDeployResult(r)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function runOnboard() {
    setBusy(true)
    setError('')
    try {
      const r = await api.onboard({ source: sourcePayload(), rows: sampleRows.slice(0, 20) })
      const d = await api.standard(r.standard_id)
      setDetail(d)
      setStandardId(r.standard_id)
      setEntityId(r.entity_id)
      setMappings(r.mappings)
      setReport(r.validation)
      setApproval(null)
      setExportResult(null)
      setDryResult(r.dry_run)
      setAgentTrace(r.trace || [])
      setOnboardInfo(`Classified as ${r.standard_id}/${r.entity_id} (confidence ${r.classification?.confidence ?? '?'}) — ${r.classification?.rationale ?? ''}`)
      setSuggestBy('agent-supervisor')
      setSuggestModel(r.classification?.model || '')
      setSuggestLlmError('')
      setStep(2)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function runRefine() {
    setBusy(true)
    setError('')
    try {
      const r = await api.refine({
        standard_id: standardId, entity_id: entityId,
        source: sourcePayload(), mappings,
      })
      setMappings(r.mappings)
      setReport(r.validation)
      setAgentTrace(r.trace || [])
      setOnboardInfo('')
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  async function runValidate() {
    setBusy(true)
    setError('')
    try {
      const r = await api.validate({
        standard_id: standardId, entity_id: entityId, source: sourcePayload(), mappings,
      })
      setReport(r)
      setStep(3)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function runApprove() {
    setBusy(true)
    setError('')
    try {
      const r = await api.approve({
        standard_id: standardId, entity_id: entityId, source: sourcePayload(),
        mappings, approved_by: approvedBy, notes,
      })
      setApproval(r.approval)
      setStep(5)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function runExport() {
    setBusy(true)
    setError('')
    try {
      const r = await api.export({
        approval_id: approval.id,
        target_catalog: catalog,
        target_schema: schema,
        output_table: table || undefined,
        source_table: sourceTable,
      })
      setExportResult(r)
      setActiveFile(0)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  function updateMapping(i, patch) {
    setMappings((ms) => ms.map((m, j) => (j === i ? { ...m, ...patch } : m)))
  }

  function removeMapping(i) {
    setMappings((ms) => ms.filter((_, j) => j !== i))
  }

  // Required-target visibility helpers for the mapping screen.
  const requiredFields = (entity?.fields || []).filter((f) => f.required)
  const targetOptions = [...(entity?.fields || [])].sort(
    (a, b) => Number(b.required || false) - Number(a.required || false)
  )
  const targetPaths = new Set((entity?.fields || []).map((f) => f.path))
  const sourceNames = columns.split(',').map((c) => c.trim()).filter(Boolean)
  const targetToMapping = {}
  mappings.forEach((m) => { if (m.target) targetToMapping[m.target] = m })
  const requiredMapped = requiredFields.filter((f) => targetToMapping[f.path])
  const targetTypeOf = {}
  ;(entity?.fields || []).forEach((f) => { targetTypeOf[f.path] = f.type })
  const [newSource, setNewSource] = useState('')
  const [newTarget, setNewTarget] = useState('')

  function addMapping() {
    if (!newSource || !newTarget) return
    setMappings((ms) => [...ms, {
      source: newSource, target: newTarget, transform: 'direct',
      confidence: 1.0, rationale: 'Added manually.',
    }])
    setNewSource('')
    setNewTarget('')
  }

  const done = [
    Boolean(standardId),
    columns.trim().length > 0,
    mappings.length > 0,
    Boolean(report),
    Boolean(approval),
    Boolean(exportResult),
  ]

  return (
    <>
      <div className="topbar">
        <div className="brand">
          <svg className="mark" viewBox="0 0 32 32" aria-hidden="true">
            <path d="M16 3 29 10v12L16 29 3 22V10z" fill="none" stroke="currentColor" strokeWidth="2.5" />
            <path d="M10 16.5l4.5 4.5L22 13" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div>
            <h1>Telemetry Foundry</h1>
            <span className="sub">Security telemetry → standard data products</span>
          </div>
        </div>
        <div className="seg" role="tablist" aria-label="Views">
          <button role="tab" className={view === 'flow' ? 'on' : ''} onClick={() => openView('flow')}>Workflow</button>
          <button role="tab" className={view === 'projects' ? 'on' : ''} onClick={() => openView('projects')}>Projects</button>
          <button role="tab" className={view === 'history' ? 'on' : ''} onClick={() => openView('history')}>History</button>
        </div>
        <span className={`mode ${mode === 'meta' ? 'llm' : ''}`} title={llmModel ? `Model: ${llmModel}` : ''}>
          {mode === 'meta' ? `Meta API · ${llmModel}` : mode === 'demo' ? 'Demo mode (offline)' : mode}
        </span>
      </div>
      <div className="layout">
        {view === 'flow' ? (<>
        <nav className="steps">
          {STEP_NAMES.map((name, i) => (
            <button
              key={name}
              className={`${step === i ? 'active' : ''} ${done[i] ? 'done' : ''}`}
              onClick={() => setStep(i)}
            >
              <span className="n">{done[i] ? '✓' : i + 1}</span> {name}
            </button>
          ))}
        </nav>
        <div className="main">
          {error && <div className="error-box">{error}</div>}

          {step === 0 && (
            <>
              <h2>1. Pick a target standard</h2>
              <p className="hint">The registry is pluggable — add a JSON file under backend/data/standards to register another standard.</p>
              <div className="cards">
                {standards.map((s) => (
                  <div
                    key={s.id}
                    className={`card ${standardId === s.id ? 'selected' : ''}`}
                    onClick={() => pickStandard(s.id)}
                  >
                    <div className="cat">{s.category}</div>
                    <h3>{s.name}</h3>
                    <p>{s.description}</p>
                    <p>{s.entities.map((e) => e.name).join(', ')}</p>
                  </div>
                ))}
              </div>
              <h2 style={{ marginTop: 24 }}>Add a standard via MCP hydration</h2>
              <p className="hint">Fetches the definition through the MCP server (agent-normalized when the Meta API is configured) into a pending queue. Activate to join the registry.</p>
              <div className="row">
                <input value={hydrateId} onChange={(e) => setHydrateId(e.target.value)} style={{ width: 240 }} placeholder="standard-id" />
                <button className="btn secondary" disabled={busy || !hydrateId.trim()} onClick={runHydrate}>
                  {busy ? 'Working…' : 'Hydrate'}
                </button>
              </div>
              {pending.length > 0 && (
                <table className="grid">
                  <thead><tr><th>Pending standard</th><th>Category</th><th></th></tr></thead>
                  <tbody>
                    {pending.map((p) => (
                      <tr key={p.id}>
                        <td><code>{p.id}</code> — {p.name}</td>
                        <td>{p.category}</td>
                        <td><button className="btn secondary" disabled={busy} onClick={() => runActivate(p.id)}>Activate</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {detail?.entities?.length > 1 && (
                <>
                  <label className="lbl">Entity</label>
                  <select value={entityId} onChange={(e) => changeEntity(e.target.value)}>
                    {detail.entities.map((e) => (
                      <option key={e.id} value={e.id}>{e.name}</option>
                    ))}
                  </select>
                </>
              )}
              <Foot>
                <button className="btn" disabled={!standardId} onClick={() => setStep(1)}>Continue</button>
              </Foot>
            </>
          )}

          {step === 1 && (
            <>
              <h2>2. Describe the source schema</h2>
              <p className="hint">Paste comma-separated column names from your extract, or keep the sample for this standard.</p>
              <label className="lbl">Source name</label>
              <input value={sourceName} onChange={(e) => setSourceName(e.target.value)} style={{ width: 300 }} />
              <label className="lbl">Columns (comma-separated)</label>
              <textarea value={columns} onChange={(e) => setColumns(e.target.value)} rows={4} style={{ width: '100%' }} />
              <div className="row">
                <label className="btn secondary" style={{ cursor: 'pointer' }}>
                  {busy ? 'Working…' : 'Upload log sample'}
                  <input type="file" accept=".csv,.tsv,.json,.jsonl,.ndjson,.log" onChange={runInfer} style={{ display: 'none' }} />
                </label>
                {inferInfo && <span className="badge">{inferInfo}</span>}
              </div>
              {Object.keys(fieldTypes).length > 0 && (
                <p className="hint">Inferred types: {Object.entries(fieldTypes).map(([k, v]) => `${k}: ${v}`).join(' · ')}</p>
              )}
              <Foot back={() => setStep(0)}>
                <button className="btn secondary" disabled={busy || !columns.trim()} onClick={runOnboard}>
                  {busy ? 'Working…' : 'Auto-onboard with agents'}
                </button>
                <button className="btn" disabled={busy || !standardId || !columns.trim()} onClick={runSuggest}>
                  {busy ? 'Working…' : 'Suggest mappings'}
                </button>
              </Foot>
            </>
          )}

          {step === 2 && (
            <>
              <h2>3. Review mapping suggestions</h2>
              <p className="hint">
                {suggestBy ? <>Generated by <span className="badge">{suggestBy}</span>{suggestModel && <> <span className="badge">{suggestModel}</span></>}. </> : null}
                {suggestLlmError && <>LLM unavailable ({suggestLlmError}); fell back to offline suggestions. </>}
                Adjust targets before validation. Empty target means unmapped.
              </p>
              <div className="req-head">
                <h3>Required targets — {requiredMapped.length} of {requiredFields.length} mapped</h3>
                <button
                  className="btn secondary"
                  disabled={busy || requiredMapped.length === requiredFields.length}
                  onClick={runFillDefaults}
                >
                  {busy ? 'Working…' : 'Auto-fill missing'}
                </button>
              </div>
              <p className="hint">Auto-fill adds constants for unmapped required fields — real OCSF UIDs where known, typed placeholders otherwise. Review each before approval.</p>
              <table className="grid">
                <thead><tr><th>Required field</th><th>Type</th><th>Status</th></tr></thead>
                <tbody>
                  {requiredFields.map((f) => (
                    <tr key={f.path}>
                      <td><code>{f.path}</code></td>
                      <td>{f.type}</td>
                      <td>
                        {targetToMapping[f.path]
                          ? <span className="badge ok">← {targetToMapping[f.path].source || targetToMapping[f.path].expression}</span>
                          : <span className="badge fail">Missing</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <h3 style={{ marginTop: 20 }}>All mappings</h3>
              <table className="grid">
                <thead>
                  <tr><th>Source</th><th>Target</th><th>Transform</th><th>Expression</th><th>Conf.</th><th>Rationale</th><th></th></tr>
                </thead>
                <tbody>
                  {mappings.map((m, i) => (
                    <tr key={i}>
                      <td>{m.source ? <code>{m.source}</code> : <span className="badge">const</span>}</td>
                      <td>
                        <select value={m.target || ''} onChange={(e) => updateMapping(i, { target: e.target.value })}>
                          <option value="">— unmapped —</option>
                          {m.target && !targetPaths.has(m.target) && (
                            <option value={m.target}>{m.target} (stale — re-pick)</option>
                          )}
                          {targetOptions.map((f) => (
                            <option key={f.path} value={f.path}>
                              {f.path}{f.required ? ' *required' : ''}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <select value={m.transform || 'direct'} onChange={(e) => updateMapping(i, { transform: e.target.value })}>
                          <option value="direct">direct</option>
                          <option value="cast">cast</option>
                          <option value="expression">expression</option>
                          <option value="constant">constant</option>
                        </select>
                      </td>
                      <td>
                        <input
                          value={m.expression || ''}
                          placeholder={EXPR_HINT[m.transform] || ''}
                          onChange={(e) => updateMapping(i, { expression: e.target.value })}
                        />
                        {m.transform === 'cast' && !m.expression && m.target && targetTypeOf[m.target] && (
                          <div className="expr-preview">
                            → CAST(`{m.source}` AS {SPARK_TYPES[targetTypeOf[m.target].toLowerCase()] || 'STRING'})
                          </div>
                        )}
                      </td>
                      <td className={`conf ${m.confidence < 0.5 ? 'low' : ''}`}>{m.confidence?.toFixed?.(2)}</td>
                      <td style={{ color: '#57534e', fontSize: 12 }}>{m.rationale}</td>
                      <td><button className="btn secondary" onClick={() => removeMapping(i)} title="Remove row">×</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="row">
                <select value={newSource} onChange={(e) => setNewSource(e.target.value)}>
                  <option value="">Source…</option>
                  {sourceNames.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
                <select value={newTarget} onChange={(e) => setNewTarget(e.target.value)}>
                  <option value="">Target…</option>
                  {targetOptions.map((f) => (
                    <option key={f.path} value={f.path}>
                      {f.path}{f.required ? ' *required' : ''}
                    </option>
                  ))}
                </select>
                <button className="btn secondary" disabled={!newSource || !newTarget} onClick={addMapping}>
                  Add mapping
                </button>
              </div>
              <h3 style={{ marginTop: 20 }}>Dry-run on sample rows</h3>
              <p className="hint">Paste a JSON array of rows{sampleRows.length > 0 ? ' or use the uploaded sample' : ''} to preview transformed output, casts included.</p>
              <textarea value={dryRowsText} onChange={(e) => setDryRowsText(e.target.value)} rows={3} style={{ width: '100%' }} placeholder='[{"event_time": "2024-01-02 03:04:05", "...": "..."}]' />
              <div className="row">
                <button className="btn secondary" disabled={busy || mappings.length === 0} onClick={() => runDryRun(false)}>Run dry-run</button>
                {sampleRows.length > 0 && <button className="btn secondary" disabled={busy} onClick={() => runDryRun(true)}>Use uploaded sample ({sampleRows.length} rows)</button>}
                {dryResult && <span className="badge">{dryResult.errors === 0 ? 'no errors' : `${dryResult.errors} cell errors`}</span>}
              </div>
              {dryResult && (
                <div style={{ overflowX: 'auto' }}>
                <table className="grid">
                  <thead><tr>{dryResult.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                  <tbody>
                    {dryResult.rows.map((r, i) => (
                      <tr key={i}>{dryResult.columns.map((c) => (
                        <td key={c} className={String(r[c] ?? '').startsWith('ERR:') ? 'err-cell' : ''}>{String(r[c] ?? '')}</td>
                      ))}</tr>
                    ))}
                  </tbody>
                </table>
                </div>
              )}
              {onboardInfo && <p className="hint">{onboardInfo}</p>}
              {agentTrace.length > 0 && (
                <div className="trace">
                  <div className="trace-head">
                    <span>Agent trace</span>
                    <button className="btn ghost" onClick={() => { setAgentTrace([]); setOnboardInfo('') }}>Clear</button>
                  </div>
                  {agentTrace.map((s, i) => (
                    <div key={i} className="trace-step">
                      <span className="badge">{s.agent}</span> {s.action}
                      {s.detail ? <span className="trace-detail"> — {s.detail}</span> : null}
                      {s.status === 'failed' ? <span className="badge fail">attention</span> : null}
                    </div>
                  ))}
                </div>
              )}
              <Foot back={() => setStep(1)}>
                <button className="btn secondary" disabled={busy || mappings.length === 0} onClick={runRefine}>
                  {busy ? 'Working…' : 'Auto-fix with critic'}
                </button>
                <button className="btn" disabled={busy || mappings.length === 0} onClick={runValidate}>
                  {busy ? 'Working…' : 'Validate mappings'}
                </button>
              </Foot>
            </>
          )}

          {step === 3 && (
            <>
              <h2>4. Validation report</h2>
              {!report && <p className="hint">Run validation from the previous step first.</p>}
              {report && (
                <>
                  <div className={`callout ${report.valid ? 'ok' : 'fail'}`}>
                    <div className="callout-title">{report.valid ? 'Mappings valid — ready for approval' : 'Resolve the errors below to continue'}</div>
                    <div className="callout-meta">
                      <span>Required coverage {(report.coverage * 100).toFixed(0)}%</span>
                      {report.breadth !== undefined && <span>Breadth {(report.breadth * 100).toFixed(0)}%</span>}
                      <span>{report.mapped_targets?.length ?? 0} targets mapped</span>
                    </div>
                    <div className="meter"><div className="meter-fill" style={{ width: `${(report.coverage * 100).toFixed(0)}%` }} /></div>
                  </div>
                  {report.errors.length > 0 && (
                    <>
                      <h3>Errors</h3>
                      <ul className="report">{report.errors.map((e, i) => <li key={i} className="error">{e}</li>)}</ul>
                    </>
                  )}
                  {report.warnings.length > 0 && (
                    <>
                      <h3>Warnings</h3>
                      <ul className="report">{report.warnings.map((e, i) => <li key={i} className="warning">{e}</li>)}</ul>
                    </>
                  )}
                  <Foot back={() => setStep(2)} backLabel="Back to mappings">
                    <button className="btn" disabled={!report.valid} onClick={() => setStep(4)}>Continue to approval</button>
                  </Foot>
                </>
              )}
            </>
          )}

          {step === 4 && (
            <>
              <h2>5. Data owner approval</h2>
              <p className="hint">Export is locked until the data owner approves the validated mapping set.</p>
              <label className="lbl">Data owner</label>
              <input value={approvedBy} onChange={(e) => setApprovedBy(e.target.value)} style={{ width: 300 }} />
              <label className="lbl">Notes (optional)</label>
              <input value={notes} onChange={(e) => setNotes(e.target.value)} style={{ width: '100%' }} />
              {report && report.warnings && report.warnings.length > 0 && (
                <>
                  <h3>Review before approving</h3>
                  <ul className="report">{report.warnings.map((w, i) => <li key={i} className="warning">{w}</li>)}</ul>
                </>
              )}
              <Foot back={() => setStep(3)}>
                <button className="btn" disabled={busy || !report?.valid} onClick={runApprove}>
                  {busy ? 'Working…' : 'Approve mappings'}
                </button>
              </Foot>
              {approval && <p>Approval <span className="badge ok">{approval.id}</span> recorded at {approval.approved_at}</p>}
            </>
          )}

          {step === 5 && (
            <>
              <h2>6. Export Databricks artifacts</h2>
              {!approval && <p className="hint">Approve the mappings first.</p>}
              {approval && (
                <>
                  <p className="hint">Approval <span className="badge">{approval.id}</span></p>
                  <div className="form-grid">
                    <div><label className="lbl">Catalog</label><input value={catalog} onChange={(e) => setCatalog(e.target.value)} /></div>
                    <div><label className="lbl">Schema</label><input value={schema} onChange={(e) => setSchema(e.target.value)} /></div>
                    <div><label className="lbl">Output table (optional)</label><input value={table} onChange={(e) => setTable(e.target.value)} placeholder="auto" /></div>
                    <div><label className="lbl">Source table</label><input value={sourceTable} onChange={(e) => setSourceTable(e.target.value)} style={{ width: '100%' }} /></div>
                  </div>
                  <Foot back={() => setStep(4)}>
                    {exportResult && <span className="badge">generated by {exportResult.generated_by}</span>}
                    {exportResult && exportResult.model && <span className="badge">{exportResult.model}</span>}
                    {exportResult && exportResult.llm_error && (
                      <span className="hint">LLM unavailable ({exportResult.llm_error}); used template instead.</span>
                    )}
                    <button className="btn" disabled={busy} onClick={runExport}>
                      {busy ? 'Working…' : 'Generate artifacts'}
                    </button>
                  </Foot>
                  {exportResult && (
                    <>
                      <p className="hint">Target: <code>{exportResult.target}</code> — deploy with <code>databricks bundle deploy</code></p>
                      <div className="tabs">
                        {exportResult.files.map((f, i) => (
                          <button key={f.path} className={i === activeFile ? 'active' : ''} onClick={() => setActiveFile(i)}>
                            {f.path}
                          </button>
                        ))}
                      </div>
                      <pre className="code">{exportResult.files[activeFile]?.content}</pre>
                      <div className="row">
                        <button className="btn secondary" onClick={() => download(exportResult.files[activeFile].path, exportResult.files[activeFile].content)}>
                          Download {exportResult.files[activeFile]?.path}
                        </button>
                        <button className="btn secondary" onClick={() => exportResult.files.forEach((f) => download(f.path, f.content))}>
                          Download all
                        </button>
                      </div>
                      <h3 style={{ marginTop: 20 }}>Deploy to Databricks</h3>
                      <p className="hint">Creates the Unity Catalog schema, imports the bundle files, and runs the SQL on your warehouse. Credentials are used once and never stored.</p>
                      <p className="hint">Target: <code>{catalog}.{schema}.{table || exportResult.target.split('.').pop()}</code> — set Catalog / Schema / Table in the fields above.</p>
                      <div className="form-grid">
                        <div><label className="lbl">Host</label><input value={depHost} onChange={(e) => setDepHost(e.target.value)} placeholder="https://xxx.cloud.databricks.com" style={{ width: '100%' }} /></div>
                        <div><label className="lbl">Token</label><input type="password" value={depToken} onChange={(e) => setDepToken(e.target.value)} style={{ width: '100%' }} /></div>
                        <div><label className="lbl">Warehouse ID</label><input value={depWarehouse} onChange={(e) => setDepWarehouse(e.target.value)} placeholder="required to run SQL" style={{ width: '100%' }} /></div>
                        <div><label className="lbl">Run SQL after deploy</label><input type="checkbox" checked={depRunSql} onChange={(e) => setDepRunSql(e.target.checked)} /></div>
                      </div>
                      <div className="row">
                        <button className="btn" disabled={busy || !depHost || !depToken} onClick={runDeploy}>
                          {busy ? 'Working…' : 'Deploy'}
                        </button>
                      </div>
                      {deployResult && <pre className="code">{JSON.stringify(deployResult, null, 2)}</pre>}
                    </>
                  )}
                </>
              )}
            </>
          )}
        </div>
        </>) : view === 'projects' ? (
        <div className="pane">
          <h2>Projects</h2>
          <p className="hint">Saved mapping drafts. Load one to continue where you left off.</p>
          <div className="row">
            <input value={projectName} onChange={(e) => setProjectName(e.target.value)} placeholder="Project name" style={{ width: 260 }} />
            <button className="btn secondary" disabled={busy || !standardId} onClick={saveProject}>Save current</button>
          </div>
          {projects.length === 0 ? <p className="hint">No saved projects yet.</p> : (
          <table className="grid">
            <thead><tr><th>Name</th><th>Standard</th><th>Entity</th><th>Mappings</th><th>Updated</th><th></th></tr></thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td><code>{p.standard_id}</code></td>
                  <td><code>{p.entity_id}</code></td>
                  <td>{p.mappings.length}</td>
                  <td>{new Date(p.updated_at).toLocaleString()}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn secondary" onClick={() => loadProject(p.id)}>Load</button>{' '}
                    <button className="btn secondary" onClick={() => overwriteProject(p.id, p.name)}>Overwrite</button>{' '}
                    <button className="btn secondary" onClick={() => removeProject(p.id, p.name)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>)}
        </div>
        ) : (
        <div className="pane">
          <h2>Approval history</h2>
          <p className="hint">Every data-owner approval, with drift re-checks against the current registry.</p>
          {approvals.length === 0 ? <p className="hint">No approvals yet.</p> : (
          <table className="grid">
            <thead><tr><th>ID</th><th>Standard</th><th>By</th><th>At</th><th>Maps</th><th></th></tr></thead>
            <tbody>
              {approvals.map((a) => (
                <tr key={a.id}>
                  <td><code>{a.id}</code></td>
                  <td><code>{a.standard_id}/{a.entity_id}</code></td>
                  <td>{a.approved_by}</td>
                  <td>{a.approved_at ? new Date(a.approved_at).toLocaleString() : ''}</td>
                  <td>{a.mapping_count}</td>
                  <td><button className="btn secondary" onClick={() => openApproval(a.id)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>)}
          {approvalDetail && (
            <>
              <h3>{approvalDetail.id} — {approvalDetail.standard_id}/{approvalDetail.entity_id}</h3>
              <p className="hint">Approved by {approvalDetail.approved_by} at {approvalDetail.approved_at ? new Date(approvalDetail.approved_at).toLocaleString() : ''}</p>
              <div className="row">
                <button className="btn secondary" disabled={busy} onClick={() => checkDrift(approvalDetail.id)}>Re-check drift</button>
                {drift && <span className={`badge ${drift.valid ? 'ok' : 'fail'}`}>{drift.valid ? 'Still valid' : 'Drifted'}</span>}
                {drift && drift.registry_changed && <span className="badge">registry changed</span>}
              </div>
              {drift && drift.missing_targets && drift.missing_targets.length > 0 && (
                <ul className="report">{drift.missing_targets.map((t) => <li key={t} className="error">Target gone from registry: {t}</li>)}</ul>
              )}
              {drift && drift.errors && drift.errors.length > 0 && (
                <ul className="report">{drift.errors.map((e, i) => <li key={i} className="error">{e}</li>)}</ul>
              )}
              <table className="grid">
                <thead><tr><th>Source</th><th>Target</th><th>Transform</th><th>Expression</th></tr></thead>
                <tbody>
                  {(approvalDetail.mappings || []).map((m, i) => (
                    <tr key={i}>
                      <td>{m.source ? <code>{m.source}</code> : <span className="badge">const</span>}</td>
                      <td><code>{m.target}</code></td>
                      <td>{m.transform}</td>
                      <td><code>{m.expression || ''}</code></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
        )}
      </div>
    </>
  )
}
