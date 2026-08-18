# E2B template for a CellGenerator constraint-development sandbox.
#
# Build (from the repository root):
#   e2b template build -c infra/e2b/e2b.toml -n cellgen-engine
#
# Three things this image has to get right, all of them load-bearing for state
# restore rather than for the solve itself:
#
#   * the engine lives at /workspace/engine and nowhere else. opencode records
#     the absolute working directory a session belongs to, and an E2B rebuild is
#     always a *new* sandbox -- so this constant path is the only thing that
#     reattaches restored sessions to their project.
#   * /workspace/engine is a git repository with a commit. opencode identifies a
#     project by its git worktree; without one every session lands in a
#     catch-all "global" project and the workspace never appears in the UI.
#     The commit is also the baseline an artifact export diffs against.
#   * XDG_DATA_HOME is set. It is the only environment variable that actually
#     moves opencode's storage (OPENCODE_DATA_DIR is ignored -- measured, see
#     docs/opencode-state-spike.md).
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node is here only to run opencode.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Pinned deliberately. State restore rides on opencode's own export/import,
# which is coupled to the version in practice; re-run the round-trip check in
# docs/opencode-state-spike.md before moving this.
RUN npm install -g opencode-ai@1.18.18

ENV XDG_DATA_HOME=/workspace/.local/share \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

WORKDIR /workspace/engine
COPY engine/ /workspace/engine/

# Installed at build time: resolving ortools inside a live sandbox costs
# minutes the user would spend staring at an empty UI.
RUN pip install --no-cache-dir -e /workspace/engine

RUN mkdir -p /workspace/engine/plugins /workspace/.local/share \
    && git -C /workspace/engine init -q \
    && git -C /workspace/engine add -A \
    && git -C /workspace/engine -c user.email=sandbox@cellgen -c user.name=CellGen \
           commit -q -m "Sandbox baseline"

# Fail the build rather than ship a template that breaks restore in a way that
# only shows up as an empty project list hours later. Each check maps to one of
# the three requirements above.
RUN set -eux; \
    test -d /workspace/engine/.git; \
    git -C /workspace/engine rev-parse HEAD; \
    test "$XDG_DATA_HOME" = /workspace/.local/share; \
    opencode --version; \
    cd /workspace/engine && python -c "import src.cellgen.run"
