async function req(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = body.detail
    const msg = typeof detail === 'string' ? detail : detail?.message || res.statusText
    if (res.status === 404 && msg === 'Not Found') {
      throw new Error('Not found on the running backend — restart the backend to pick up the latest API.')
    }
    throw new Error(msg)
  }
  return body
}

const post = (path, payload, method = 'POST') =>
  req(path, { method, body: JSON.stringify(payload) })

export const api = {
  health: () => req('/api/health'),
  standards: () => req('/api/standards'),
  standard: (id) => req(`/api/standards/${id}`),
  suggest: (payload) => post('/api/suggest', payload),
  validate: (payload) => post('/api/validate', payload),
  approve: (payload) => post('/api/approve', payload),
  export: (payload) => post('/api/export', payload),
  pending: () => req('/api/standards-pending'),
  hydrate: (standard_id) => post('/api/standards/hydrate', { standard_id }),
  activate: (id) => post(`/api/standards/${id}/activate`, {}),
  defaults: (payload) => post('/api/defaults', payload),
  dryrun: (payload) => post('/api/dryrun', payload),
  projects: () => req('/api/projects'),
  createProject: (payload) => post('/api/projects', payload),
  updateProject: (id, payload) => post(`/api/projects/${id}`, payload, 'PUT'),
  deleteProject: (id) => req(`/api/projects/${id}`, { method: 'DELETE' }),
  approvals: () => req('/api/approvals'),
  approval: (id) => req(`/api/approvals/${id}`),
  drift: (id) => req(`/api/approvals/${id}/drift`),
  deploy: (payload) => post('/api/deploy', payload),
  refine: (payload) => post('/api/agent/refine', payload),
  onboard: (payload) => post('/api/agent/onboard', payload),
  infer: async (file) => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/infer', { method: 'POST', body: form })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(body.detail || res.statusText)
    return body
  },
}
