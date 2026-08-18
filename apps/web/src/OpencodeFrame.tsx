/**
 * The opencode UI, embedded.
 *
 * The iframe points at `proxy_url` -- a per-sandbox reverse proxy on its own
 * port -- and never at the sandbox itself. Two reasons, both load-bearing:
 * the proxy injects the sandbox's credentials server-side so the browser never
 * holds them, and opencode's UI loads its assets from root-absolute paths, so
 * it cannot be served from under a subpath of this app.
 *
 * That makes the iframe a different origin from the app shell. Embedding needs
 * no CORS, and the UI's own requests are same-origin *within* the frame, so
 * nothing breaks -- but it does mean this component cannot see inside it.
 */

import type { Phase } from './useSandbox'

function Placeholder({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2
                    bg-slate-950 text-center">
      <p className="text-sm font-medium text-slate-300">{title}</p>
      <p className="max-w-md text-xs leading-relaxed text-slate-500">{detail}</p>
    </div>
  )
}

export function OpencodeFrame({ url, phase }: { url?: string | null; phase: Phase }) {
  if (phase === 'offline') {
    return (
      <Placeholder
        title="連不上 API"
        detail="後端沒有在 http://localhost:8000 回應。啟動它:
                uvicorn cellgen_api.main:app --port 8000 --app-dir services/api"
      />
    )
  }
  if (phase === 'paused') {
    return (
      <Placeholder
        title="工作區已暫停"
        detail="暫停前已經做過快照,對話不會遺失。按上方「恢復」把它叫回來。"
      />
    )
  }
  if (phase === 'failed') {
    return (
      <Placeholder
        title="沙盒啟動失敗"
        detail="看 .cellgen/sandboxes/workspace/opencode.log。最常見的原因是
                CELLGEN_OPENCODE_BIN 指到不存在的檔案,或那個埠已被佔用。"
      />
    )
  }
  if (!url) {
    return (
      <Placeholder
        title="正在準備工作區…"
        detail="複製 engine、起 opencode,並把最近一份快照放回去。"
      />
    )
  }
  return (
    <iframe
      src={url}
      title="opencode"
      className="h-full w-full border-0 bg-white"
      // The sandbox runs the agent; the frame needs to do everything a normal
      // page does. Restricting it here would only break the UI, and it buys
      // nothing: the proxy is what actually contains the sandbox.
      allow="clipboard-read; clipboard-write"
    />
  )
}
