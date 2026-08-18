"""Following a run's log while it is still being written.

A solve can run for hours, so the useful question is not "what did it print"
but "what is it printing now". Two sources answer that together:

* the log file the worker writes, which has everything from the start;
* the Redis channel the worker publishes each line to, which has everything
  from now.

Replaying the file first and then following the channel is what lets someone
open a run halfway through and see the whole thing. The seam can duplicate a
line or two -- a line written between the read and the subscribe -- which is a
far better failure than a gap.

Server-sent events rather than WebSockets: the stream is one-directional, SSE
reconnects on its own, and it survives an ordinary HTTP proxy.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

from loguru import logger

# Published by the worker when a run finishes, so a follower can stop waiting.
END_MARKER = "__END__"


def channel(run_id: str) -> str:
    return f"run:{run_id}:log"


def _redis_url() -> str:
    return os.environ.get("CELLGEN_REDIS_URL", "redis://127.0.0.1:6379/0")


def sse(data: str, event: str | None = None) -> str:
    """Frame one server-sent event.

    Every line of the payload needs its own `data:` prefix, and the blank line
    is what ends the event -- a log line containing a newline would otherwise
    silently truncate the stream.
    """
    out = f"event: {event}\n" if event else ""
    for line in data.split("\n"):
        out += f"data: {line}\n"
    return out + "\n"


async def follow(run_id: str, log_path: Path | None,
                 idle_timeout: float = 300.0) -> AsyncIterator[str]:
    """Yield SSE frames: the log so far, then each new line as it arrives."""
    if log_path is not None and log_path.is_file():
        history = log_path.read_text(errors="replace")
        if history.strip():
            yield sse(history.rstrip("\n"), event="history")

    client = None
    pubsub = None
    try:
        import redis.asyncio as aioredis

        client = aioredis.Redis.from_url(
            _redis_url(), decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel(run_id))
    except Exception as exc:
        # Without Redis there is no live tail, only what is already on disk.
        # Saying so beats a stream that silently never updates.
        logger.warning(f"[{run_id}] cannot follow live log: {exc}")
        yield sse("live streaming unavailable: no Redis to follow", event="warning")
        yield sse("", event="end")
        return

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=idle_timeout,
                )
            except asyncio.TimeoutError:
                # Nothing for a long while: the worker is gone, or the run
                # ended without publishing its marker.
                yield sse("", event="end")
                return

            if message is None:
                # No traffic this second. A comment keeps the connection warm
                # through proxies that would otherwise time it out.
                yield ": keepalive\n\n"
                continue

            line = message.get("data", "")
            if line == END_MARKER:
                yield sse("", event="end")
                return
            yield sse(line)
    finally:
        for closer in (pubsub, client):
            if closer is not None:
                try:
                    await closer.aclose()
                except Exception:
                    pass
