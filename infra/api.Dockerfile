# Shared by the API and the worker: both need the engine importable and the
# same solver dependencies, and building one image keeps them in step.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The API starts opencode itself when CELLGEN_BACKEND=local. Pinned for the
# same reason as the E2B template: state restore rides on its export/import.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g opencode-ai@1.18.18

WORKDIR /app
COPY engine/ /app/engine/
COPY services/ /app/services/

# Engine first: it carries ortools, which dominates the build.
RUN pip install --no-cache-dir -e "/app/engine[mcp]" \
    && pip install --no-cache-dir -e /app/services/api \
    && pip install --no-cache-dir -e /app/services/worker

ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PYTHONPATH=/app/services/api:/app/services/worker

EXPOSE 8000
CMD ["uvicorn", "cellgen_api.main:app", "--host=0.0.0.0", "--port=8000", \
     "--app-dir=/app/services/api"]
