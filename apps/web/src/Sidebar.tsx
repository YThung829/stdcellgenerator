/**
 * What the agent has produced, and whether it still solves.
 *
 * The plugin list is re-read from the sandbox rather than cached: the agent
 * edits those files directly, so anything held here would be stale the moment
 * it does.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Plugin, type SmokeResult } from './api'
import { SANDBOX_ID } from './useSandbox'

function Section({ title, action, children }: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="border-b border-slate-800 px-3 py-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[11px] font-semibold tracking-wide text-slate-400 uppercase">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  )
}

function Action({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className="rounded px-2 py-0.5 text-[11px] font-medium text-slate-300
                 ring-1 ring-slate-700 transition hover:bg-slate-800
                 hover:text-slate-100 disabled:cursor-not-allowed
                 disabled:opacity-35 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  )
}

function PluginRow({ plugin }: { plugin: Plugin }) {
  const name = plugin.path.replace(/^plugins\//, '')
  return (
    <li className="rounded-md px-2 py-1.5 ring-1 ring-slate-800">
      <div className="flex items-baseline gap-2">
        <span className="truncate font-mono text-xs text-slate-200">{name}</span>
        {!plugin.enabled && !plugin.error && (
          <span className="shrink-0 text-[10px] text-slate-500">已停用</span>
        )}
      </div>

      {plugin.error ? (
        <p className="mt-1 text-[11px] text-rose-400">{plugin.error}</p>
      ) : (
        <>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px]">
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-400">
              {plugin.stage}
            </span>
            {plugin.tech.map((t) => (
              <span key={t} className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-500">
                {t}
              </span>
            ))}
          </div>
          {Object.keys(plugin.params).length > 0 && (
            // The manifest's values, not the decorator's defaults -- those are
            // what the engine will actually use.
            <p className="mt-1 font-mono text-[10px] text-slate-500">
              {Object.entries(plugin.params)
                .map(([k, v]) => `${k}=${String(v)}`)
                .join('  ')}
            </p>
          )}
        </>
      )}
    </li>
  )
}

function SmokePanel({ result, pending, onRun, disabled }: {
  result?: SmokeResult
  pending: boolean
  onRun: () => void
  disabled: boolean
}) {
  return (
    <Section
      title="驗證"
      action={
        <Action onClick={onRun} disabled={disabled || pending}>
          {pending ? '解題中…' : '跑 smoke'}
        </Action>
      }
    >
      {!result ? (
        <p className="text-[11px] leading-relaxed text-slate-500">
          用目前的 plugin 解一顆 INV_X1,確認引擎還解得動、objective 是多少。
        </p>
      ) : (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <span
              className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                result.ok
                  ? 'bg-emerald-400/15 text-emerald-300'
                  : 'bg-rose-400/15 text-rose-300'
              }`}
            >
              {result.status || 'ERROR'}
            </span>
            <span className="font-mono text-[11px] text-slate-400">{result.cell}</span>
            {result.elapsed !== null && (
              <span className="ml-auto font-mono text-[11px] text-slate-500">
                {result.elapsed.toFixed(2)}s
              </span>
            )}
          </div>
          {/* Objective only; two runs of the same model can produce different
              layouts at identical cost, so geometry is not comparable. */}
          <p className="font-mono text-[11px] text-slate-400">
            objective:{' '}
            <span className="text-slate-200">
              {result.objective === null ? '—' : result.objective}
            </span>
          </p>
          {!result.ok && result.log_tail && (
            <pre className="max-h-32 overflow-auto rounded bg-slate-950 p-2
                            font-mono text-[10px] leading-relaxed text-slate-500">
              {result.log_tail.split('\n').slice(-8).join('\n')}
            </pre>
          )}
        </div>
      )}
    </Section>
  )
}

/**
 * `lastSmoke` arrives as a prop rather than from a query of its own: the
 * sandbox record is already polled one level up, and registering a second
 * query function against that same key would leave the two fighting over it.
 */
export function Sidebar({ ready, lastSmoke }: {
  ready: boolean
  lastSmoke?: SmokeResult
}) {
  const qc = useQueryClient()
  const [name, setName] = useState('')

  const plugins = useQuery({
    queryKey: ['plugins', SANDBOX_ID],
    queryFn: () => api.plugins(SANDBOX_ID),
    enabled: ready,
    refetchInterval: 5_000,
  })

  const smoke = useMutation({
    mutationFn: () => api.smoke(SANDBOX_ID),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sandbox', SANDBOX_ID] }),
  })

  const exportArtifact = useMutation({
    mutationFn: () => api.exportArtifact(SANDBOX_ID, name.trim()),
    onSuccess: () => {
      setName('')
      qc.invalidateQueries({ queryKey: ['artifacts'] })
    },
  })

  const artifacts = useQuery({
    queryKey: ['artifacts'],
    queryFn: api.artifacts,
    enabled: ready,
  })

  const list = plugins.data?.plugins ?? []
  // The just-run result wins over the one stored on the record.
  const result = smoke.data ?? lastSmoke

  return (
    <aside className="flex w-80 shrink-0 flex-col overflow-y-auto border-l
                      border-slate-800 bg-slate-900">
      <Section title={`Plugins (${list.length})`}>
        {list.length === 0 ? (
          <p className="text-[11px] leading-relaxed text-slate-500">
            還沒有 constraint plugin。在左邊用自然語言請 opencode 寫一條,
            它會照 <code className="text-slate-400">AGENTS.md</code> 的介面產生檔案。
          </p>
        ) : (
          <ul className="space-y-1.5">
            {list.map((p) => (
              <PluginRow key={p.path} plugin={p} />
            ))}
          </ul>
        )}
      </Section>

      <SmokePanel
        result={result}
        pending={smoke.isPending}
        onRun={() => smoke.mutate()}
        disabled={!ready}
      />

      <Section title="匯出">
        <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
          沙盒是可拋棄的工作副本。匯出是把成果帶出去的唯一路徑。
        </p>
        <div className="flex gap-1.5">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="artifact 名稱"
            className="min-w-0 flex-1 rounded bg-slate-950 px-2 py-1 font-mono
                       text-[11px] text-slate-200 ring-1 ring-slate-800
                       outline-none placeholder:text-slate-600
                       focus:ring-slate-600"
          />
          <Action
            onClick={() => exportArtifact.mutate()}
            disabled={!ready || !name.trim() || exportArtifact.isPending}
          >
            {exportArtifact.isPending ? '匯出中…' : '匯出'}
          </Action>
        </div>
        {exportArtifact.error && (
          <p className="mt-1.5 text-[11px] text-rose-400">
            {(exportArtifact.error as Error).message}
          </p>
        )}

        {(artifacts.data?.length ?? 0) > 0 && (
          <ul className="mt-2.5 space-y-1">
            {artifacts.data!.slice(0, 6).map((a) => (
              <li key={a._id} className="flex items-baseline gap-2 text-[11px]">
                <span className="truncate text-slate-300">{a.name}</span>
                <span className="ml-auto shrink-0 font-mono text-[10px] text-slate-600">
                  {a.plugin_count} plugin
                  {a.patch_bytes > 0 && ` · ${a.patch_bytes}B patch`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </aside>
  )
}
