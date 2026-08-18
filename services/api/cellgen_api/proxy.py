"""Same-origin reverse proxy to a sandbox's opencode server.

The frontend embeds opencode's web UI in an iframe. Pointing that iframe
straight at the sandbox would mean cross-origin requests, CORS configuration on
the sandbox, and -- worse -- handing the browser the sandbox's server password
so it could authenticate.

Proxying through the API instead makes the iframe same-origin, keeps the
password server-side, and means the sandbox never has to accept traffic from a
browser origin at all.
"""

from __future__ import annotations

import base64

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

# Hop-by-hop headers must not be forwarded (RFC 9110 7.6.1); `host` has to be
# dropped so the upstream sees its own, and encoding/length are recomputed.
_STRIP_REQUEST = {
    "host", "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    # Never forward the browser's own auth; we substitute the sandbox's.
    "authorization",
}
_STRIP_RESPONSE = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "content-encoding", "content-length",
}


async def proxy_request(request: Request, base_url: str, path: str,
                        password: str | None) -> Response:
    """Forward one HTTP request to the sandbox and stream the response back."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _STRIP_REQUEST}
    body = await request.body()

    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    req = client.build_request(
        request.method, url,
        headers=headers,
        content=body or None,
        params=dict(request.query_params),
    )
    if password:
        # opencode's server auth is HTTP basic with a fixed username.
        token = base64.b64encode(f"opencode:{password}".encode()).decode()
        req.headers["Authorization"] = f"Basic {token}"

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
        body_stream(),
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )
