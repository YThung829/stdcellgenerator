"""Reverse proxy to a sandbox's opencode server.

The frontend embeds opencode's web UI in an iframe. Pointing that iframe
directly at the sandbox would hand the browser the sandbox's address and its
server password, so the traffic is proxied instead and the password stays
server-side.

**Why each sandbox gets its own proxy port rather than a path under the API.**
The obvious design -- ``/api/sandboxes/{id}/oc/{path}`` -- does not work.
opencode's UI references its assets with root-absolute URLs::

    <script src="/assets/index-CQtwhDOb.js">
    <link href="/assets/index-CMLUT3g5.css">

Under a subpath mount the browser resolves those against the API root, not the
mount point, so every asset 404s and the UI never loads. ``<base href>`` does
not help: it does not affect URLs that already begin with ``/``, and the bundle
builds more URLs at runtime anyway.

Giving each sandbox a listener whose *root* is the proxy makes the problem
disappear -- paths pass through unchanged. The iframe is then a different
origin from the app shell, which is fine: embedding needs no CORS, and the UI's
own requests are same-origin with respect to the iframe.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable

import httpx
import uvicorn
from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

# Hop-by-hop headers must not be forwarded (RFC 9110 7.6.1). `host` is dropped
# so the upstream sees its own; content-length/encoding are recomputed.
_STRIP_REQUEST = {
    "host", "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    # Never forward the browser's credentials; the proxy supplies its own.
    "authorization",
}
_STRIP_RESPONSE = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    # `content-length` is dropped because the response is re-chunked.
    # `content-encoding` is deliberately *kept*: the body is forwarded raw
    # (see `aiter_raw` below), so stripping the header would hand the browser
    # compressed bytes labelled as plain JSON.
    "content-length",
}


class SandboxProxy:
    """A root-path reverse proxy for one sandbox, on its own ephemeral port."""

    def __init__(self, base_url: str, password: str | None, host: str = "127.0.0.1",
                 on_activity: Callable[[], None] | None = None):
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.host = host
        # Traffic through here is the truest signal that someone is using the
        # workspace: the embedded UI polls constantly while a tab is open, and
        # goes silent the moment it is not.
        self.on_activity = on_activity
        self.port: int | None = None
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    @property
    def url(self) -> str | None:
        return f"http://{self.host}:{self.port}" if self.port else None

    def _auth_header(self) -> str | None:
        if not self.password:
            return None
        token = base64.b64encode(f"opencode:{self.password}".encode()).decode()
        return f"Basic {token}"

    async def _handle(self, request: Request) -> Response:
        if self.on_activity is not None:
            self.on_activity()
        url = f"{self.base_url}/{request.path_params['path'].lstrip('/')}"
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in _STRIP_REQUEST}
        body = await request.body()

        # The body is forwarded raw, so the upstream must never use an encoding
        # the browser did not ask for. httpx would otherwise supply its own
        # default `accept-encoding` when the client sent none.
        headers.setdefault("accept-encoding", "identity")

        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        req = client.build_request(
            request.method, url, headers=headers,
            content=body or None, params=dict(request.query_params),
        )
        if (auth := self._auth_header()):
            req.headers["Authorization"] = auth

        try:
            upstream = await client.send(req, stream=True)
        except httpx.RequestError as exc:
            await client.aclose()
            return Response(f"sandbox unreachable: {exc}", status_code=502)

        out_headers = {k: v for k, v in upstream.headers.items()
                       if k.lower() not in _STRIP_RESPONSE}

        async def body_stream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            body_stream(), status_code=upstream.status_code, headers=out_headers,
            media_type=upstream.headers.get("content-type"),
        )

    def _app(self) -> Starlette:
        methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
        return Starlette(routes=[
            Route("/{path:path}", self._handle, methods=methods),
        ])

    async def start(self) -> str:
        """Bind an ephemeral port and start serving. Returns the proxy URL."""
        config = uvicorn.Config(
            self._app(), host=self.host, port=0, log_level="warning",
            # opencode's UI streams events; buffering would stall the UI.
            timeout_keep_alive=75,
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())

        # uvicorn publishes the bound socket only once serving has begun.
        for _ in range(200):
            if self._server.started and self._server.servers:
                socks = self._server.servers[0].sockets
                if socks:
                    self.port = socks[0].getsockname()[1]
                    logger.info(f"sandbox proxy listening on {self.url} -> {self.base_url}")
                    return self.url  # type: ignore[return-value]
            await asyncio.sleep(0.05)
        raise RuntimeError("sandbox proxy failed to bind a port")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._server, self._task, self.port = None, None, None
