import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
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

function Studio() {
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
      />

      {error && phase !== 'offline' && (
        <p className="border-b border-rose-900/60 bg-rose-950/40 px-4 py-1.5
                      font-mono text-xs text-rose-300">
          {error.message}
        </p>
      )}

      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1">
          <OpencodeFrame url={sandbox?.proxy_url} phase={phase} />
        </main>
        <Sidebar ready={phase === 'ready'} lastSmoke={sandbox?.last_smoke} />
      </div>
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
