/**
 * Typed client for the CellGenerator Studio API.
 *
 * Everything the UI knows about a sandbox comes from here. Note what is
 * deliberately absent: the sandbox's address and its opencode password. The
 * browser never sees either -- it only ever gets `proxy_url`, a same-machine
 * reverse proxy that injects the credentials server-side.
 */

const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export type SandboxState = 'starting' | 'running' | 'paused' | 'stopped' | 'failed'

export interface SmokeResult {
  ok: boolean
  status: string
  objective: number | null
  elapsed: number | null
  cell: string
  preset: string
  command: string
  log_tail: string
  finished_at?: number
}

export interface Sandbox {
  _id: string
  backend: string
  state: SandboxState
  workdir: string
  /** Where to point the iframe. Null whenever the sandbox is not running. */
  proxy_url: string | null
  healthy?: boolean
  restored_sessions?: number
  last_snapshot_id?: string
  last_smoke?: SmokeResult
  created_at: number
  updated_at: number
}

export interface Plugin {
  path: string
  id: string
  stage: string
  description: string
  tech: string[]
  params: Record<string, unknown>
  enabled: boolean
  /** Non-empty when the file could not be parsed; it is still listed. */
  error: string
}

export interface PluginList {
  plugins: Plugin[]
  manifest: unknown | null
}

export interface Snapshot {
  _id: string
  sandbox_id: string
  session_count: number
  size_bytes: number
  created_at: number
}

export interface Artifact {
  _id: string
  sandbox_id: string
  name: string
  description: string
  plugin_count: number
  patch_bytes: number
  changed_files: string[]
  size_bytes: number
  created_at: number
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    // A network-level failure here almost always means the API is not running,
    // which is worth saying plainly rather than surfacing "Failed to fetch".
    throw new ApiError(0, `cannot reach the API at ${BASE}`)
  }
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new ApiError(resp.status, detail || `${resp.status} ${resp.statusText}`)
  }
  return resp.status === 204 ? (undefined as T) : resp.json()
}

export const api = {
  health: () => call<{ ok: boolean; backend: string }>('/api/health'),

  listSandboxes: () => call<Sandbox[]>('/api/sandboxes'),

  getSandbox: (id: string) => call<Sandbox>(`/api/sandboxes/${id}`),

  /** Create, or rebuild an id that already exists. A live id is returned as-is. */
  createSandbox: (sandboxId: string, restoreFrom?: string) =>
    call<Sandbox>('/api/sandboxes', {
      method: 'POST',
      body: JSON.stringify({ sandbox_id: sandboxId, restore_from: restoreFrom }),
    }),

  pause: (id: string) => call<Sandbox>(`/api/sandboxes/${id}/pause`, { method: 'POST' }),

  resume: (id: string) => call<Sandbox>(`/api/sandboxes/${id}/resume`, { method: 'POST' }),

  destroy: (id: string) =>
    call<void>(`/api/sandboxes/${id}?keep_snapshots=true`, { method: 'DELETE' }),

  snapshot: (id: string) =>
    call<Snapshot>(`/api/sandboxes/${id}/snapshot`, { method: 'POST' }),

  snapshots: (id: string) => call<Snapshot[]>(`/api/sandboxes/${id}/snapshots`),

  plugins: (id: string) => call<PluginList>(`/api/sandboxes/${id}/plugins`),

  smoke: (id: string, body: { cell?: string; maxTime?: number } = {}) =>
    call<SmokeResult>(`/api/sandboxes/${id}/smoke`, {
      method: 'POST',
      body: JSON.stringify({ cell: body.cell, max_time: body.maxTime }),
    }),

  exportArtifact: (id: string, name: string, description = '') =>
    call<Artifact>(`/api/sandboxes/${id}/export`, {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    }),

  artifacts: () => call<Artifact[]>('/api/artifacts'),
}
