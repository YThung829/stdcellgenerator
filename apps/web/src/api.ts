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


export type RunStatus = 'pending' | 'running' | 'done' | 'failed' | 'cancelled'

export interface PluginDelta {
  id: string
  stage: string
  params: Record<string, unknown>
  constraints_added: number
  variables_added: number
}

export interface Run {
  _id: string
  experiment_id: string
  cell: string
  status: RunStatus
  /** The engine's own word for the outcome: `ok`, `INFEASIBLE`, `ERROR`. */
  solver_status?: string
  /** Only ever set for a solve that succeeded -- see the executor. */
  objective?: number | null
  walltime?: number | null
  metrics?: Record<string, number>
  artifacts?: Partial<Record<'res' | 'var' | 'png' | 'gds' | 'log', string>>
  plugin_deltas?: PluginDelta[]
  error?: string
  celery_task_id?: string
  /** Present when the run could not be queued; it stays pending. */
  dispatch_error?: string
  created_at: number
}

export interface Experiment {
  _id: string
  name: string
  description: string
  artifact_id: string | null
  preset: string
  cells: string[]
  overrides: string[]
  max_time: number
  /** Derived from the runs, never stored. */
  status: RunStatus
  created_at: number
  runs: Run[]
  run_count?: number
  done_count?: number
}

export interface NewExperiment {
  name: string
  preset: string
  cells: string[]
  artifact_id?: string | null
  description?: string
  overrides?: string[]
  max_time?: number
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

  /**
   * Ask for a pause on the way out, without waiting for the answer.
   *
   * `fetch` is cancelled when the page goes away, so a plain call from an
   * unload handler usually never leaves the browser. `sendBeacon` is handed to
   * the browser to deliver on its own time. It cannot set headers, so the body
   * is empty and the endpoint takes none.
   *
   * This is an optimisation, not the mechanism: the server reclaims idle
   * workspaces regardless, which is what covers a crashed tab or a closed
   * laptop.
   */
  pauseOnExit: (id: string): boolean => {
    if (typeof navigator === 'undefined' || !navigator.sendBeacon) return false
    return navigator.sendBeacon(`${BASE}/api/sandboxes/${id}/pause`)
  },

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

  // -- experiments ----------------------------------------------------

  listExperiments: () => call<Experiment[]>('/api/experiments'),

  getExperiment: (id: string) => call<Experiment>(`/api/experiments/${id}`),

  createExperiment: (body: NewExperiment) =>
    call<Experiment>('/api/experiments', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  cancelExperiment: (id: string) =>
    call<Run[]>(`/api/experiments/${id}/cancel`, { method: 'POST' }),

  getRun: (id: string) => call<Run>(`/api/runs/${id}`),

  cancelRun: (id: string) => call<Run>(`/api/runs/${id}/cancel`, { method: 'POST' }),

  /** Where to fetch one of a run's output files. */
  runArtifactUrl: (id: string, kind: string) =>
    `${BASE}/api/runs/${id}/artifacts/${kind}`,

  /** The SSE endpoint a run's log is followed on. */
  runLogUrl: (id: string) => `${BASE}/api/runs/${id}/log`,
}
