"""Mount the MCP servers into the FastAPI app over Streamable HTTP.

Each server is exposed at its own path (e.g. ``/mcp/derivative-income``) on the SAME host
and port as the web app, so a remote agent (Gemini CLI) connects to a URL rather than
launching a local subprocess.

Access control (optional): if ``MCP_AUTH_TOKEN`` (env) is set, every MCP request must send
it as ``Authorization: Bearer <token>`` or ``X-API-Key: <token>``. Unset ⇒ open (fine for
a private/local deployment; set the token whenever the port is reachable from the internet).

Transport notes:
  * ``stateless_http=True`` — each request is self-contained (no server-side session state),
    which suits the "call once per ticker, hundreds of times" pattern and any number of
    workers / a load balancer.
  * ``json_response=True`` — replies are plain ``application/json`` (no SSE stream), the
    friendliest shape for simple HTTP clients.
  * DNS-rebinding protection is disabled: it defends browsers calling localhost servers,
    which is not this threat model (a CLI hitting a server API). The bearer token is the
    access control.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from starlette.types import Receive, Scope, Send

from mcp.server.transport_security import TransportSecuritySettings

log = logging.getLogger("app.mcp.http_mount")


def _registered_servers() -> list[tuple[str, Any]]:
    """Registry of (mount_path, MCPServer instance). Add future servers here.

    Uses RELATIVE imports so it works whether the package root is ``app`` (stdio launcher)
    or ``backend.app`` (the web app's uvicorn entrypoint ``backend.app.main:app``)."""
    from .derivative_income import mcp as derivative_income_mcp
    return [
        ("/mcp/derivative-income", derivative_income_mcp),
    ]


class _BearerAuthASGI:
    """Tiny ASGI gate: require a shared secret before the request reaches the MCP app."""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token
        self._bearer = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        authorized = (
            headers.get(b"authorization", b"").decode() == self._bearer
            or headers.get(b"x-api-key", b"").decode() == self._token
        )
        if authorized:
            await self._app(scope, receive, send)
            return
        body = b'{"error":"unauthorized: missing or invalid MCP token"}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer")],
        })
        await send({"type": "http.response.body", "body": body})


def mount_mcp_servers(app: Any) -> list[Any]:
    """Mount every registered MCP server onto *app*. Returns the list of MCPServer
    instances whose ``session_manager`` must be run for the app's lifetime (see
    ``app.main.lifespan``). Streamable-HTTP apps create their session manager lazily on
    first ``streamable_http_app()`` call, so we build the ASGI app here and hand back the
    live instance."""
    token = os.environ.get("MCP_AUTH_TOKEN") or os.environ.get("DERIVINC_MCP_TOKEN") or ""
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    instances: list[Any] = []
    for path, mcp in _registered_servers():
        asgi = mcp.streamable_http_app(
            streamable_http_path="/",       # endpoint == the mount path (no inner segment)
            stateless_http=True,
            json_response=True,
            transport_security=security,
        )
        if token:
            asgi = _BearerAuthASGI(asgi, token)
        app.mount(path, asgi)
        instances.append(mcp)
        log.info("Mounted MCP server %s at %s (auth=%s)", mcp.name, path, "token" if token else "open")
    if not token:
        log.warning("MCP servers mounted WITHOUT auth. Set MCP_AUTH_TOKEN to require a bearer token.")
    return instances
