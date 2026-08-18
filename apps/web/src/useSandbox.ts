/**
 * Getting the workspace ready, and keeping the UI honest about its state.
 *
 * Tab 1 is a single-workspace tool, so the sandbox id is a constant. That is
 * not cosmetic: opencode scopes a session to its absolute working directory,
 * and the backend derives that directory from the id -- so reusing the same id
 * is what lets a rebuilt sandbox pick its old conversations back up.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { ApiError, api, type Sandbox } from './api'

export const SANDBOX_ID = 'workspace'

/** How the UI describes what is happening, which is not the same as backend state. */
export type Phase = 'connecting' | 'restoring' | 'ready' | 'paused' | 'failed' | 'offline'

/** True for a record that claims to be running but has nothing behind it.
 *
 * The stored record outlives the process that served it, so after an API
 * restart it still says `running` and still carries the previous proxy port --
 * a port nothing is listening on. Only `healthy`, which the API re-checks per
 * request, distinguishes that from a sandbox that is genuinely up. `undefined`
 * means not checked (a lifecycle call's response), not unhealthy.
 */
export function isStale(sandbox: Sandbox | undefined | null): boolean {
  return !!sandbox && sandbox.state === 'running' && sandbox.healthy === false
}

export function phaseOf(sandbox: Sandbox | undefined, isWorking: boolean,
                        offline: boolean): Phase {
  if (offline) return 'offline'
  if (isWorking) return sandbox ? 'restoring' : 'connecting'
  if (!sandbox) return 'connecting'
  // Rebuilding is already under way; do not offer a dead iframe meanwhile.
  if (isStale(sandbox)) return 'restoring'
  if (sandbox.state === 'running') return 'ready'
  if (sandbox.state === 'paused') return 'paused'
  return 'failed'
}

export function useSandbox() {
  const qc = useQueryClient()
  const booted = useRef(false)

  const health = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    retry: false,
    refetchInterval: 15_000,
  })

  // Fetched by id rather than from the listing: only this endpoint reports
  // `healthy`, and without it a stale record is indistinguishable from a live
  // sandbox.
  const sandbox = useQuery({
    queryKey: ['sandbox', SANDBOX_ID],
    queryFn: async () => {
      try {
        return await api.getSandbox(SANDBOX_ID)
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null
        throw err
      }
    },
    enabled: health.isSuccess,
    refetchInterval: 10_000,
  })

  // POST /api/sandboxes is the single "make it ready" call: it creates a new
  // sandbox, returns a live one untouched, and rebuilds a lost one from its
  // most recent snapshot. Resume is only needed after an explicit pause.
  const ensure = useMutation({
    mutationFn: () => api.createSandbox(SANDBOX_ID),
    onSuccess: (s) => qc.setQueryData(['sandbox', SANDBOX_ID], s),
  })

  const resume = useMutation({
    mutationFn: () => api.resume(SANDBOX_ID),
    onSuccess: (s) => qc.setQueryData(['sandbox', SANDBOX_ID], s),
  })

  const pause = useMutation({
    mutationFn: () => api.pause(SANDBOX_ID),
    onSuccess: (s) => qc.setQueryData(['sandbox', SANDBOX_ID], s),
  })

  const rebuild = useMutation({
    mutationFn: async () => {
      await api.destroy(SANDBOX_ID) // snapshots are kept
      return api.createSandbox(SANDBOX_ID)
    },
    onSuccess: (s) => {
      qc.setQueryData(['sandbox', SANDBOX_ID], s)
      qc.invalidateQueries({ queryKey: ['plugins'] })
    },
  })

  // Boot once the first fetch has come back. Three cases need a sandbox built:
  // nothing exists yet, the record is paused or failed, or it claims to be
  // running but nothing answers -- the last one being what an API restart
  // leaves behind.
  const record = sandbox.data
  useEffect(() => {
    if (!sandbox.isSuccess || booted.current) return
    booted.current = true
    if (!record || record.state !== 'running' || isStale(record)) ensure.mutate()
  }, [sandbox.isSuccess, record, ensure])

  const isWorking = ensure.isPending || resume.isPending || rebuild.isPending
  const phase = phaseOf(record ?? undefined, isWorking, health.isError)

  return {
    sandbox: record ?? undefined,
    phase,
    backend: health.data?.backend,
    error: (ensure.error ?? resume.error ?? rebuild.error ?? health.error) as Error | null,
    actions: { ensure, resume, pause, rebuild },
  }
}
