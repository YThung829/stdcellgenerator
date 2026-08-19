/**
 * Following a run's log while it is still being written.
 *
 * `EventSource` rather than a polled fetch: the server already replays the
 * history and then tails the live channel, and the browser reconnects on its
 * own if the connection drops.
 *
 * The stream is closed on unmount and whenever the run changes. Leaving one
 * open holds a connection per run the user has ever glanced at, and the server
 * side of it holds a Redis subscription.
 */

import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

// A long solve prints a great deal; keeping every line makes the tab crawl.
const MAX_LINES = 2000

export function RunLog({ runId }: { runId: string }) {
  const [lines, setLines] = useState<string[]>([])
  const [state, setState] = useState<'connecting' | 'live' | 'ended' | 'error'>(
    'connecting')
  const bottom = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)

  useEffect(() => {
    setLines([])
    setState('connecting')

    const source = new EventSource(api.runLogUrl(runId))
    const append = (chunk: string) => {
      setLines((current) => {
        const next = [...current, ...chunk.split('\n')]
        return next.length > MAX_LINES ? next.slice(-MAX_LINES) : next
      })
    }

    // Everything written before this follower arrived, as one frame.
    source.addEventListener('history', (e) => {
      append((e as MessageEvent).data)
      setState('live')
    })
    source.addEventListener('warning', (e) => {
      append(`— ${(e as MessageEvent).data} —`)
    })
    source.addEventListener('end', () => {
      setState('ended')
      source.close()
    })
    source.onmessage = (e) => {
      append(e.data)
      setState('live')
    }
    source.onerror = () => {
      // EventSource retries on its own; only a closed stream is terminal.
      if (source.readyState === EventSource.CLOSED) setState('error')
    }

    return () => source.close()
  }, [runId])

  // Follow the tail, unless the reader has scrolled up to look at something.
  useEffect(() => {
    if (pinned.current) bottom.current?.scrollIntoView({ block: 'end' })
  }, [lines])

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[11px] font-semibold tracking-wide text-slate-400 uppercase">
          Log
        </span>
        <span className={`text-[10px] ${
          state === 'live' ? 'text-emerald-400'
            : state === 'error' ? 'text-rose-400' : 'text-slate-500'}`}>
          {{ connecting: '連線中…', live: '● 即時', ended: '已結束',
             error: '連線中斷' }[state]}
        </span>
        <a
          href={api.runArtifactUrl(runId, 'log')}
          className="ml-auto text-[10px] text-slate-500 hover:text-slate-300"
        >
          下載完整 log
        </a>
      </div>
      <div
        onScroll={onScroll}
        className="min-h-0 flex-1 overflow-auto bg-slate-950 px-3 py-2
                   font-mono text-[10px] leading-relaxed text-slate-400"
      >
        {lines.length === 0 && state !== 'error' && (
          <p className="text-slate-600">還沒有輸出。</p>
        )}
        {lines.map((line, i) => (
          <div key={i} className={
            line.startsWith('status:') || line.includes('Objective value')
              ? 'text-slate-200' : undefined
          }>
            {line || ' '}
          </div>
        ))}
        <div ref={bottom} />
      </div>
    </div>
  )
}
