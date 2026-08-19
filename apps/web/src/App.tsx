import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { Experiments } from './experiments/Experiments'
import { OpencodeFrame } from './OpencodeFrame'
import { SandboxBar } from './SandboxBar'
import { Sidebar } from './Sidebar'
import { useSandbox } from './useSandbox'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Sandbox state changes underneath us (a solve finishes, the agent
      // writes a file), so polled data is never treated as fresh.
      staleTime: 0,
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
})

type Tab = 'develop' | 'experiment'

function Tabs({ tab, onChange }: { tab: Tab; onChange: (t: Tab) => void }) {
  return (
    <div className="flex items-center gap-1">
      {([['develop', '開發區'], ['experiment', '實驗區']] as const).map(
        ([key, label]) => (
          <button
            key={key}
            onClick={() => onChange(key)}
            className={`rounded px-2.5 py-1 text-xs font-medium transition ${
              tab === key
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {label}
          </button>
        ))}
    </div>
  )
}

function Studio() {
  const [tab, setTab] = useState<Tab>('develop')
  const { sandbox, phase, backend, error, actions } = useSandbox()
  const busy = actions.ensure.isPending || actions.resume.isPending
    || actions.pause.isPending || actions.rebuild.isPending

  return (
    <div className="flex h-full flex-col bg-slate-950 text-slate-200">
      <SandboxBar
        sandbox={sandbox}
        phase={phase}
        backend={backend}
        busy={busy}
        onPause={() => actions.pause.mutate()}
        onResume={() => actions.resume.mutate()}
        onRebuild={() => actions.rebuild.mutate()}
        tabs={<Tabs tab={tab} onChange={setTab} />}
        // The sandbox controls belong to the development area; showing them
        // beside an experiment would invite pausing the workspace by mistake.
        showSandboxControls={tab === 'develop'}
      />

      {error && phase !== 'offline' && tab === 'develop' && (
        <p className="border-b border-rose-900/60 bg-rose-950/40 px-4 py-1.5
                      font-mono text-xs text-rose-300">
          {error.message}
        </p>
      )}

      {tab === 'develop' ? (
        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1">
            <OpencodeFrame url={sandbox?.proxy_url} phase={phase} />
          </main>
          <Sidebar ready={phase === 'ready'} lastSmoke={sandbox?.last_smoke} />
        </div>
      ) : (
        <Experiments />
      )}
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Studio />
    </QueryClientProvider>
  )
}
