# Spike: how to persist and restore OpenCode sandbox state

**Date:** 2026-08-18 · **opencode version tested:** `1.18.18` (npm `opencode-ai`)

Phase 0 of the plan called for settling this before building the sandbox layer,
because the answer determines what "restore the conversation" costs us. Three of
the plan's assumptions turned out to be wrong, so the design changed.

## Findings

### 1. `OPENCODE_DATA_DIR` does not exist — the knob is `XDG_DATA_HOME`

The plan assumed `OPENCODE_DATA_DIR` would pin the storage location. It is
ignored. Measured:

| env var | resulting `opencode db path` |
|---|---|
| *(none)* | `~/.local/share/opencode/opencode.db` |
| `OPENCODE_DATA_DIR=<dir>` | unchanged — **ignored** |
| `OPENCODE_HOME` / `OPENCODE_CONFIG_DIR` / `OPENCODE_STATE_DIR` | unchanged — ignored |
| `XDG_DATA_HOME=<dir>` | `<dir>/opencode/opencode.db` ✅ |

**Use `XDG_DATA_HOME`.** `opencode db path` prints the resolved location, so the
sandbox can assert this at boot instead of trusting the convention.

### 2. There is a supported export/import interface — don't copy the SQLite file

The plan proposed tarring the SQLite DB. Unnecessary and fragile: v1.18.18 ships

```
opencode export <sessionID>   # session JSON on stdout
opencode import <file>        # restore from that JSON
```

Verified end to end: exported a session from one data dir, imported it into a
**completely empty** one, and the session came back with its **id preserved**.

This is a public, versioned interface rather than an internal schema, so it
survives opencode upgrades far better than a DB copy would. It also sidesteps
SQLite WAL handling (a data dir carries `opencode.db`, `-wal` and `-shm`; copying
the `.db` alone can lose recent writes).

Export schema is `{"info": {...}, "messages": [...]}` — the conversation rides in
`messages`.

**Caveat:** `opencode export` with no session ID blocks on an interactive
picker. Always pass an explicit id; enumerate first via `GET /session` or
`opencode session list`.

### 3. Footprint: ~600 B per empty session vs 308 KB for the data dir

| artifact | size |
|---|---|
| exported session JSON (empty session) | **634 B** |
| a fresh data dir (`opencode.db` + `-wal` + `-shm` + log) | 308 KB |

Session JSON scales with conversation length, not with the engine or its deps.
This is the "越精簡越好" answer: persist session JSON, nothing else.

### 4. Session scoping confirms the fixed-workdir requirement

A created session carries:

```json
{ "projectID": "089b22d1...7603",
  "directory": "/private/tmp/.../proj" }
```

`projectID` is derived from the absolute working directory (it is *not* a plain
SHA-1 of the path, so treat it as opaque). The consequence stands either way:
**the sandbox working directory must be byte-identical across rebuilds** or
restored sessions will not be associated with the project. Pin it to
`/workspace/engine`.

### 5. `opencode serve` is usable headless with no provider configured

`opencode serve --port 4096 --hostname 127.0.0.1` starts and serves the REST API
immediately. `GET /session` returns `[]`; `POST /session` creates a session
without any model call — useful for health checks and for tests that must not
spend tokens.

It warns `OPENCODE_SERVER_PASSWORD is not set; server is unsecured`, confirming
that variable is what enables basic auth.

## Resulting design

Snapshot = **session JSON per session**, not a filesystem tarball:

1. Enumerate sessions (`GET /session`).
2. `opencode export <id>` for each; store the JSON in MongoDB.
3. Restore into a fresh sandbox with `opencode import` per session, with
   `XDG_DATA_HOME` pinned and the workdir at `/workspace/engine`.

Plugin sources under the workdir are versioned separately as constraint
documents, so they do not belong in this snapshot.

## Residual risks

- **Sessions with real messages were not round-tripped.** No provider is
  configured here, so the exercised export had `messages: []`. The mechanism is
  proven; the payload is not. Re-run this round trip against a real conversation
  as the first check once a provider key exists in the sandbox.
- **`export`/`import` are version-coupled in practice.** Pin the opencode
  version in the E2B template and re-run this round trip when upgrading it.

## Reproducing

```bash
npm i opencode-ai@1.18.18
export OC=./node_modules/.bin/opencode
XDG_DATA_HOME=/tmp/dataA $OC db path
cd /your/project && XDG_DATA_HOME=/tmp/dataA $OC serve --port 4096 &
curl -s -X POST localhost:4096/session -H 'Content-Type: application/json' -d '{"title":"t"}'
XDG_DATA_HOME=/tmp/dataA $OC export <id> > s.json
XDG_DATA_HOME=/tmp/dataB $OC import s.json
XDG_DATA_HOME=/tmp/dataB $OC session list
```
