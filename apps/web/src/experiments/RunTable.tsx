/**
 * The runs of one experiment, sortable, and the detail of the selected one.
 *
 * Comparison here is by **metric only**. Two runs of the same model can settle
 * on different layouts at an identical objective -- measured, four runs, one
 * objective, three geometries -- so a side-by-side of the images would show
 * differences that mean nothing. The layout is for inspecting one result; the
 * numbers are for comparing two.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api, type Experiment, type Run, type RunStatus } from '../api'
import { RunLog } from './RunLog'

const STATUS_STYLE: Record<RunStatus, string> = {
  pending: 'text-slate-400',
  running: 'text-amber-300',
  done: 'text-emerald-300',
  failed: 'text-rose-300',
  cancelled: 'text-slate-500',
}

type SortKey = 'cell' | 'status' | 'objective' | 'walltime'

function value(run: Run, key: SortKey): string | number {
  if (key === 'objective') return run.objective ?? Number.POSITIVE_INFINITY
  if (key === 'walltime') return run.walltime ?? Number.POSITIVE_INFINITY
  return run[key] ?? ''
}

function Header({ label, sortKey, sort, onSort, align = 'left' }: {
  label: string
  sortKey: SortKey
  sort: { key: SortKey; asc: boolean }
  onSort: (k: SortKey) => void
  align?: 'left' | 'right'
}) {
  return (
    <th
      onClick={() => onSort(sortKey)}
      className={`cursor-pointer px-2 py-1 font-medium select-none hover:text-slate-200
                  ${align === 'right' ? 'text-right' : 'text-left'}`}
    >
      {label}
      {sort.key === sortKey && (
        <span className="ml-0.5 text-slate-500">{sort.asc ? '↑' : '↓'}</span>
      )}
    </th>
  )
}

function RunDetail({ run }: { run: Run }) {
  const qc = useQueryClient()
  const cancel = useMutation({
    mutationFn: () => api.cancelRun(run._id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['experiments'] }),
  })

  const active = run.status === 'pending' || run.status === 'running'

  return (
    <div className="flex min-h-0 flex-1 flex-col border-t border-slate-800">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <span className="font-mono text-xs text-slate-200">{run.cell}</span>
        <span className={`text-[11px] ${STATUS_STYLE[run.status]}`}>
          {run.status}
          {run.solver_status && run.solver_status !== 'ok' && ` · ${run.solver_status}`}
        </span>
        {run.objective != null && (
          <span className="font-mono text-[11px] text-slate-400">
            objective <span className="text-slate-200">{run.objective}</span>
          </span>
        )}
        {run.walltime != null && (
          <span className="font-mono text-[11px] text-slate-500">
            {run.walltime.toFixed(1)}s
          </span>
        )}
        {active && (
          <button
            onClick={() => cancel.mutate()}
            disabled={cancel.isPending}
            className="ml-auto rounded px-2 py-0.5 text-[11px] text-slate-300
                       ring-1 ring-slate-700 hover:bg-slate-800 disabled:opacity-40"
          >
            取消
          </button>
        )}
      </div>

      {run.dispatch_error && (
        <p className="px-3 pb-2 text-[11px] text-amber-400">
          尚未排入佇列：{run.dispatch_error}
        </p>
      )}

      {(run.plugin_deltas?.length ?? 0) > 0 && (
        <div className="px-3 pb-2">
          <p className="text-[10px] text-slate-500">這次 run 的 plugin 實際加了：</p>
          {run.plugin_deltas!.map((d) => (
            <p key={d.id} className="font-mono text-[10px] text-slate-400">
              {d.id} · +{d.constraints_added} constraint
              {d.variables_added > 0 && ` · +${d.variables_added} var`}
            </p>
          ))}
        </div>
      )}

      {run.artifacts?.png && (
        <div className="px-3 pb-2">
          {/* One result, inspected. Never two side by side -- see the module note. */}
          <img
            src={api.runArtifactUrl(run._id, 'png')}
            alt={`${run.cell} layout`}
            className="max-h-64 rounded bg-white object-contain"
          />
        </div>
      )}

      <RunLog runId={run._id} />
    </div>
  )
}

export function RunTable({ experiment }: { experiment: Experiment }) {
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>(
    { key: 'cell', asc: true })
  const [selected, setSelected] = useState<string | null>(null)

  const runs = useMemo(() => {
    const sorted = [...experiment.runs].sort((a, b) => {
      const [x, y] = [value(a, sort.key), value(b, sort.key)]
      if (x === y) return a.cell.localeCompare(b.cell)
      return (x < y ? -1 : 1) * (sort.asc ? 1 : -1)
    })
    return sorted
  }, [experiment.runs, sort])

  const onSort = (key: SortKey) =>
    setSort((s) => ({ key, asc: s.key === key ? !s.asc : true }))

  const current = runs.find((r) => r._id === selected) ?? runs[0]

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="max-h-64 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 bg-slate-900 text-[10px] text-slate-500 uppercase">
            <tr>
              <Header label="Cell" sortKey="cell" sort={sort} onSort={onSort} />
              <Header label="Status" sortKey="status" sort={sort} onSort={onSort} />
              <Header label="Objective" sortKey="objective" sort={sort}
                      onSort={onSort} align="right" />
              <Header label="Walltime" sortKey="walltime" sort={sort}
                      onSort={onSort} align="right" />
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr
                key={run._id}
                onClick={() => setSelected(run._id)}
                className={`cursor-pointer border-t border-slate-800/70 transition
                            hover:bg-slate-800/40
                            ${current?._id === run._id ? 'bg-slate-800/60' : ''}`}
              >
                <td className="px-2 py-1 font-mono text-slate-200">{run.cell}</td>
                <td className={`px-2 py-1 ${STATUS_STYLE[run.status]}`}>
                  {run.status}
                </td>
                <td className="px-2 py-1 text-right font-mono text-slate-300">
                  {run.objective ?? '—'}
                </td>
                <td className="px-2 py-1 text-right font-mono text-slate-500">
                  {run.walltime != null ? `${run.walltime.toFixed(1)}s` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {current && <RunDetail run={current} />}
    </div>
  )
}
