/** Workspace state and the three things you can do to it. */

import { SANDBOX_ID, type Phase } from './useSandbox'
import type { Sandbox } from './api'

const PHASE_LABEL: Record<Phase, string> = {
  connecting: 'connecting',
  restoring: 'restoring',
  ready: 'running',
  paused: 'paused',
  failed: 'failed',
  offline: 'API offline',
}

const PHASE_STYLE: Record<Phase, string> = {
  connecting: 'bg-amber-400/15 text-amber-300 ring-amber-400/30',
  restoring: 'bg-amber-400/15 text-amber-300 ring-amber-400/30',
  ready: 'bg-emerald-400/15 text-emerald-300 ring-emerald-400/30',
  paused: 'bg-slate-400/15 text-slate-300 ring-slate-400/30',
  failed: 'bg-rose-400/15 text-rose-300 ring-rose-400/30',
  offline: 'bg-rose-400/15 text-rose-300 ring-rose-400/30',
}

function Button({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className="rounded-md px-2.5 py-1 text-xs font-medium text-slate-300
                 ring-1 ring-slate-700 transition hover:bg-slate-800
                 hover:text-slate-100 disabled:cursor-not-allowed
                 disabled:opacity-35 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  )
}

interface Props {
  sandbox?: Sandbox
  phase: Phase
  backend?: string
  busy: boolean
  onPause: () => void
  onResume: () => void
  onRebuild: () => void
  tabs?: React.ReactNode
  /** The workspace state and its controls only mean anything in Tab 1. */
  showSandboxControls?: boolean
}

export function SandboxBar({ sandbox, phase, backend, busy, onPause, onResume,
                             onRebuild, tabs, showSandboxControls = true }: Props) {
  const live = phase === 'ready'
  return (
    <header className="flex items-center gap-3 border-b border-slate-800
                       bg-slate-900 px-4 py-2.5">
      <span className="text-sm font-semibold text-slate-100">CellGenerator Studio</span>
      {tabs}

      {showSandboxControls && (
      <span
        className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5
                    text-xs font-medium ring-1 ring-inset ${PHASE_STYLE[phase]}`}
      >
        <span
          className={`h-1.5 w-1.5 rounded-full bg-current
                      ${phase === 'restoring' || phase === 'connecting' ? 'animate-pulse' : ''}`}
        />
        {PHASE_LABEL[phase]}
      </span>
      )}

      {showSandboxControls &&
        <code className="text-xs text-slate-500">{SANDBOX_ID}</code>}
      {backend && (
        <span className="text-xs text-slate-600">
          backend: <span className="text-slate-500">{backend}</span>
        </span>
      )}
      {showSandboxControls && sandbox?.restored_sessions ? (
        <span className="text-xs text-emerald-500/80">
          已還原 {sandbox.restored_sessions} 段對話
        </span>
      ) : null}

      {showSandboxControls && (
        <div className="ml-auto flex items-center gap-2">
          {/* Pausing snapshots first, so it is safe to leave; resume re-derives
              the sandbox address, which changes across the cycle. */}
          <Button onClick={onPause} disabled={!live || busy}>暫停</Button>
          <Button onClick={onResume} disabled={live || busy}>恢復</Button>
          <Button onClick={onRebuild} disabled={busy}>重建</Button>
        </div>
      )}
    </header>
  )
}
