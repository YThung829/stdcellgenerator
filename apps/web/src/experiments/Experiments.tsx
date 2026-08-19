/** Tab 2: create experiments, watch them run, compare what came back. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Experiment, type RunStatus } from '../api'
import { NewExperiment } from './NewExperiment'
import { RunTable } from './RunTable'

const STATUS_CHIP: Record<RunStatus, string> = {
  pending: 'bg-slate-400/15 text-slate-300',
  running: 'bg-amber-400/15 text-amber-300',
  done: 'bg-emerald-400/15 text-emerald-300',
  failed: 'bg-rose-400/15 text-rose-300',
  cancelled: 'bg-slate-500/15 text-slate-400',
}

function ExperimentRow({ experiment, selected, onSelect }: {
  experiment: Experiment
  selected: boolean
  onSelect: () => void
}) {
  const done = experiment.done_count ?? 0
  const total = experiment.run_count ?? experiment.cells.length
  return (
    <button
      onClick={onSelect}
      className={`w-full border-b border-slate-800/70 px-3 py-2 text-left transition
                  hover:bg-slate-800/40 ${selected ? 'bg-slate-800/60' : ''}`}
    >
      <div className="flex items-center gap-2">
        <span className="truncate text-xs text-slate-200">{experiment.name}</span>
        <span className={`ml-auto shrink-0 rounded px-1.5 py-0.5 text-[10px]
                          font-medium ${STATUS_CHIP[experiment.status]}`}>
          {experiment.status}
        </span>
      </div>
      <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-slate-500">
        <span>{experiment.preset}</span>
        <span>{done}/{total}</span>
      </div>
    </button>
  )
}

export function Experiments() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const list = useQuery({
    queryKey: ['experiments'],
    queryFn: api.listExperiments,
    // Runs finish in another process, so the list is polled rather than
    // waiting for something to tell us.
    refetchInterval: 5_000,
  })

  const id = selectedId ?? list.data?.[0]?._id ?? null

  const detail = useQuery({
    queryKey: ['experiments', id],
    queryFn: () => api.getExperiment(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      // Stop polling once nothing can change; a finished experiment is static.
      const status = query.state.data?.status
      return status === 'running' || status === 'pending' ? 2_000 : false
    },
  })

  const cancel = useMutation({
    mutationFn: () => api.cancelExperiment(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['experiments'] })
    },
  })

  const experiment = detail.data
  const active = experiment?.status === 'running' || experiment?.status === 'pending'

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="flex w-72 shrink-0 flex-col overflow-y-auto border-r
                        border-slate-800 bg-slate-900">
        <NewExperiment onCreated={setSelectedId} />
        <div className="px-3 py-2">
          <h2 className="text-[11px] font-semibold tracking-wide text-slate-400 uppercase">
            實驗 ({list.data?.length ?? 0})
          </h2>
        </div>
        {(list.data ?? []).map((e) => (
          <ExperimentRow
            key={e._id}
            experiment={e}
            selected={e._id === id}
            onSelect={() => setSelectedId(e._id)}
          />
        ))}
        {list.data?.length === 0 && (
          <p className="px-3 text-[11px] leading-relaxed text-slate-500">
            還沒有實驗。挑一個 artifact 和幾顆 cell 就能開始。
          </p>
        )}
      </aside>

      <main className="flex min-w-0 flex-1 flex-col bg-slate-950">
        {!experiment ? (
          <div className="flex h-full items-center justify-center">
            <p className="max-w-sm text-center text-xs leading-relaxed text-slate-500">
              左邊建立一個實驗。每顆 cell 會是一個 run，各自解、各自可取消。
              比較請看 objective 與 walltime——同一個模型跑兩次，版面可能不同而
              成本相同。
            </p>
          </div>
        ) : (
          <>
            <header className="flex flex-wrap items-center gap-2 border-b
                               border-slate-800 px-3 py-2">
              <span className="text-sm font-medium text-slate-100">
                {experiment.name}
              </span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium
                                ${STATUS_CHIP[experiment.status]}`}>
                {experiment.status}
              </span>
              <span className="font-mono text-[11px] text-slate-500">
                {experiment.preset} · max_time {experiment.max_time}s
              </span>
              {experiment.artifact_id && (
                <span className="font-mono text-[10px] text-slate-600">
                  {experiment.artifact_id}
                </span>
              )}
              {active && (
                <button
                  onClick={() => cancel.mutate()}
                  disabled={cancel.isPending}
                  className="ml-auto rounded px-2 py-0.5 text-[11px] text-slate-300
                             ring-1 ring-slate-700 hover:bg-slate-800
                             disabled:opacity-40"
                >
                  全部取消
                </button>
              )}
            </header>
            <RunTable experiment={experiment} />
          </>
        )}
      </main>
    </div>
  )
}
