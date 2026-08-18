# CellGenerator Studio API

Backend for Tab 1 (constraint development): sandbox lifecycle, conversation
state capture, and a same-origin proxy to the sandbox's opencode UI.

## Running

```bash
pip install -e services/api[dev]
CELLGEN_OPENCODE_BIN=$(which opencode) \
  uvicorn cellgen_api.main:app --reload --port 8000 --app-dir services/api
```

Open <http://localhost:8000/docs> for the generated API reference.

## Backends

`CELLGEN_BACKEND` selects where sandboxes live.

- **`local`** (default) runs opencode as a subprocess on this machine. No
  isolation — development only — but it exercises the same state capture,
  proxy and lifecycle code as E2B, so the whole layer is testable without
  credentials.
- **`e2b`** runs them in E2B sandboxes. Needs `E2B_API_KEY` and a template
  (`CELLGEN_E2B_TEMPLATE`). Not yet exercised against the live service.

## Configuration

| variable | default | meaning |
|---|---|---|
| `CELLGEN_BACKEND` | `local` | `local` or `e2b` |
| `CELLGEN_ENGINE_SRC` | `../../engine` | engine checkout copied into local sandboxes |
| `CELLGEN_DATA_ROOT` | `../../.cellgen` | sandboxes and the file store |
| `CELLGEN_OPENCODE_BIN` | `opencode` on PATH | opencode binary |
| `CELLGEN_MONGO_URL` | unset | use MongoDB; falls back to files if unreachable |
| `CELLGEN_CORS_ORIGINS` | `http://localhost:5173` | comma-separated frontend origins |
| `E2B_API_KEY` | unset | required by the e2b backend |

## Reaching the opencode UI

Each running sandbox gets its own reverse proxy on an ephemeral localhost port,
reported as `proxy_url` (also available from
`GET /api/sandboxes/{id}/proxy`). The frontend iframes that URL.

It is a separate port rather than a path under this API because opencode's UI
loads its assets from root-absolute URLs (`/assets/index-*.js`) and calls its
own `/api/*` and `/global/*` endpoints — under a subpath mount every asset
404s, and its `/api/*` routes would collide with this service's. The proxy
injects the sandbox's basic-auth credentials, so the browser never receives
the sandbox address or its password; requesting the sandbox directly returns
401.

The sandbox working directory is initialised as a git repository. opencode
identifies a *project* by its git worktree; without one it files every session
under a catch-all "global" project and the workspace never appears in the UI.

## How state survives a sandbox

A sandbox is disposable. What persists is the exported opencode session JSON —
hundreds of bytes per session, versus ~308 KB for the data directory it lives
in — captured through opencode's own `export`/`import` commands rather than by
copying its SQLite file. See `docs/opencode-state-spike.md` for the
measurements behind that choice.

`POST /pause` snapshots before suspending, because a paused sandbox is not a
backup. If one is lost, `POST /api/sandboxes` with the same `sandbox_id`
rebuilds it and replays the latest snapshot.

Reusing the `sandbox_id` matters: opencode scopes a session to its absolute
working directory, so a rebuild has to land on the same path for restored
sessions to attach to the project.

## Tests

```bash
cd services/api && CELLGEN_OPENCODE_BIN=$(which opencode) python -m pytest -q
```

Tests that need a sandbox skip cleanly when no opencode binary is present.
