/**
 * Starting an experiment: an artifact, a preset, and the cells to solve.
 *
 * The time cap is on the form rather than buried in settings on purpose. It
 * and `use_relative_gap` are the only two knobs that trade quality for
 * iteration speed, and the engine's own default is *no limit* -- an
 * experiment left uncapped holds a worker slot indefinitely.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type NewExperiment as NewExperimentBody } from '../api'

const PRESETS = ['FinFET_4T_SH', 'CFET_4T_SH', 'QFET_4T_SH']

// Small, fast, and representative -- the three the engine's own regression
// checks use. Anything in the netlist can be typed in instead.
const SUGGESTED_CELLS = ['INV_X1', 'NAND2_X1', 'NOR2_X1', 'AOI21_X2', 'DFFHQN_X1']

function Field({ label, hint, children }: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="text-[11px] font-medium text-slate-400">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[10px] text-slate-600">{hint}</span>}
    </label>
  )
}

const inputClass =
  'mt-1 w-full rounded bg-slate-950 px-2 py-1.5 font-mono text-xs text-slate-200 ' +
  'ring-1 ring-slate-800 outline-none placeholder:text-slate-600 focus:ring-slate-600'

export function NewExperiment({ onCreated }: { onCreated: (id: string) => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [preset, setPreset] = useState(PRESETS[0])
  const [cells, setCells] = useState<string[]>(['INV_X1'])
  const [cellText, setCellText] = useState('')
  const [artifactId, setArtifactId] = useState('')
  const [maxTime, setMaxTime] = useState(300)
  const [overrides, setOverrides] = useState('')

  const artifacts = useQuery({ queryKey: ['artifacts'], queryFn: api.artifacts })

  const create = useMutation({
    mutationFn: () => {
      const body: NewExperimentBody = {
        name: name.trim() || `${preset} · ${cells.length} cells`,
        preset,
        cells,
        artifact_id: artifactId || null,
        max_time: maxTime,
        overrides: overrides.split(/\s+/).filter(Boolean),
      }
      return api.createExperiment(body)
    },
    onSuccess: (experiment) => {
      qc.invalidateQueries({ queryKey: ['experiments'] })
      onCreated(experiment._id)
    },
  })

  const toggleCell = (cell: string) =>
    setCells((current) =>
      current.includes(cell) ? current.filter((c) => c !== cell) : [...current, cell])

  const addTyped = () => {
    const typed = cellText.split(/[\s,]+/).filter(Boolean)
    if (!typed.length) return
    setCells((current) => [...new Set([...current, ...typed])])
    setCellText('')
  }

  return (
    <div className="space-y-3 border-b border-slate-800 p-3">
      <h2 className="text-[11px] font-semibold tracking-wide text-slate-400 uppercase">
        新實驗
      </h2>

      <Field label="名稱">
        <input value={name} onChange={(e) => setName(e.target.value)}
               placeholder="留空自動命名" className={inputClass} />
      </Field>

      <Field label="Preset">
        <select value={preset} onChange={(e) => setPreset(e.target.value)}
                className={inputClass}>
          {PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </Field>

      <Field
        label="Artifact"
        hint="不選就跑未修改的引擎，作為對照組"
      >
        <select value={artifactId} onChange={(e) => setArtifactId(e.target.value)}
                className={inputClass}>
          <option value="">（不套用 artifact）</option>
          {(artifacts.data ?? []).map((a) => (
            <option key={a._id} value={a._id}>
              {a.name} · {a.plugin_count} plugin
            </option>
          ))}
        </select>
      </Field>

      <Field label={`Cells (${cells.length})`}>
        <div className="mt-1 flex flex-wrap gap-1">
          {[...new Set([...SUGGESTED_CELLS, ...cells])].map((cell) => (
            <button
              key={cell}
              type="button"
              onClick={() => toggleCell(cell)}
              className={`rounded px-1.5 py-0.5 font-mono text-[10px] ring-1 transition ${
                cells.includes(cell)
                  ? 'bg-sky-400/15 text-sky-300 ring-sky-400/30'
                  : 'text-slate-500 ring-slate-800 hover:text-slate-300'
              }`}
            >
              {cell}
            </button>
          ))}
        </div>
        <div className="mt-1.5 flex gap-1.5">
          <input
            value={cellText}
            onChange={(e) => setCellText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTyped() } }}
            placeholder="其他 cell，空白分隔"
            className={inputClass.replace('mt-1 ', '')}
          />
          <button type="button" onClick={addTyped}
                  className="shrink-0 rounded px-2 text-[11px] text-slate-300
                             ring-1 ring-slate-700 hover:bg-slate-800">
            加入
          </button>
        </div>
      </Field>

      <div className="grid grid-cols-2 gap-2">
        <Field label="max_time (秒)" hint="引擎預設無上限">
          <input type="number" min={0} value={maxTime}
                 onChange={(e) => setMaxTime(Number(e.target.value))}
                 className={inputClass} />
        </Field>
        <Field label="Config overrides" hint="例如 seed=7">
          <input value={overrides} onChange={(e) => setOverrides(e.target.value)}
                 placeholder="KEY=VALUE …" className={inputClass} />
        </Field>
      </div>

      <button
        onClick={() => create.mutate()}
        disabled={!cells.length || create.isPending}
        className="w-full rounded bg-sky-500/15 py-1.5 text-xs font-medium
                   text-sky-300 ring-1 ring-sky-400/30 transition
                   hover:bg-sky-500/25 disabled:cursor-not-allowed
                   disabled:opacity-40 disabled:hover:bg-sky-500/15"
      >
        {create.isPending ? '排程中…' : `跑 ${cells.length} 個 run`}
      </button>

      {create.error && (
        <p className="text-[11px] text-rose-400">{(create.error as Error).message}</p>
      )}
    </div>
  )
}
