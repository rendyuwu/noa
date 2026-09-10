"""NOA side of the LibreChat embed render gate (items (d) and (f) of the render-path check).

This is a **verification harness**, not product code. It exists to answer one question with a live
run rather than a source read: when LibreChat at pin `45cc53c4` renders a NOA-served `text/uri-list`
UI resource, does the resulting document sit on NOA's origin with the `noa_session` cookie riding
into it, so the approve POST — cookie plus CSRF, never the LLM — is possible?

Three things about the shape here are deliberate.

**Production code is not touched.** The probe tools live on a *second* FastMCP server mounted
at `/mcp-embed-probe`. `register_mcp_tools` refuses any name outside `TOOL_CATALOG` — catalog,
RBAC and audit bound at one seam — which is exactly the guard that should stop a throwaway tool
from entering the real
registry — so the harness stands up its own server instead of arguing with it. The real
`/mcp` mount is built here unchanged, from the same `build_mcp_http_app`, so the app this
process serves is NOA's app plus a probe surface, not a simulation of it.

**Authentication is real.** The probe server takes the same `NoaTokenVerifier` and the same
`McpAuthErrorMiddleware` as production, so LibreChat has to present a minted bearer token and the
`X-Noa-LibreChat-User` header the sole-client design requires, and TOFU binding is exercised live
for the first time. What the probe server does *not* carry is `RbacToolMiddleware`: its tools are
uncatalogued by construction, so the RBAC gate would refuse them — correctly, and uselessly for this
question.

**The frame page is the measurement.** `/embed-probe/frame` is served from NOA's origin and does
what the real approval card will have to do: a same-origin `GET /auth/me` and a same-origin `POST
/embed-probe/decide`, both `credentials: 'include'`, both by JS `fetch` because a native form submit
is dead in that sandbox — `allow-forms` absent at every render site. The verdicts land in `data-*`
attributes so the browser probe reads a value rather than a screenshot.

`/embed-probe/decide` stands in for the approve endpoint and deliberately implements none of it: no
CSRF token, no row lock, no reason. It answers "did the cookie arrive" and nothing else. The gate
and the decision endpoints build the real thing once this gate has chosen a branch.

Run:

    uv run python spikes/librechat-embed-render-gate/noa_embed_probe_server.py
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastmcp import Context, FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.lifespan import combine_lifespans
from mcp.types import EmbeddedResource, TextContent, TextResourceContents
from pydantic import AnyUrl
from starlette.middleware import Middleware

from core.config import get_settings
from noa_api.api.deps import SessionUserDep
from noa_api.api.errors import install_error_handling
from noa_api.api.routes.auth import router as auth_router
from noa_api.main import build_lifespan, build_runtime
from noa_api.mcp_auth import NoaTokenVerifier
from noa_api.mcp_request_auth import McpAuthErrorMiddleware, build_mcp_auth_context
from noa_api.mcp_server import MCP_MOUNT_PATH, build_mcp_http_app

# Where LibreChat points its `mcpServers.noa` entry, and the path inside the sub-app once
# Starlette has stripped the mount prefix — same pairing, and same reason, as `mcp_server`.
PROBE_MOUNT_PATH = "/mcp-embed-probe"
PROBE_APP_PATH = "/"

# The host the frame is served from. Different origin from the LibreChat parent
# (`chat.noa.internal`), same registrable domain — which is the whole point: `SameSite=Lax`
# makes the session cookie a same-site cookie here, and the deployment is built that way —
# every operator origin under one registrable parent. Overridable so a re-run on other hostnames
# does not need an edit.
EMBED_ORIGIN = os.environ.get("NOA_EMBED_PROBE_ORIGIN", "http://embed.noa.internal:8000")

# Where the JSON-RPC method log lands. Under `.runtime/` beside the harness, which is
# gitignored — it is a measurement, not an artifact worth keeping in the tree.
JSONRPC_LOG_PATH = os.environ.get(
    "NOA_JSONRPC_LOG", str(Path(__file__).resolve().parent / ".runtime" / "jsonrpc.log")
)

TOOL_URI_LIST = "embed_probe_uri_list"
TOOL_HTML = "embed_probe_html"
TOOL_NOTIFY = "embed_probe_notify_tools_changed"


def _frame_html(*, action_request_id: str, mode: str) -> str:
    """The NOA-origin document under test.

    Reports four things into `data-*` attributes, in this order of interest:

    - `data-origin` — where the browser thinks this document lives. On the `src` path that
      is NOA's origin; on the `srcDoc` path it is `null` (an opaque origin), which is the
      failure the embed-on-NOA-origin design exists to prevent.
    - `data-cookie-visible` — `document.cookie`. Must stay empty: the session cookie is
      httpOnly, so a value here would mean something else is setting a readable one.
    - `data-me` / `data-decide` — the two requests that matter. A 200 on both means the
      cookie rode into the frame and a decision POST from this document would reach NOA
      authenticated. A 401 means it did not.

    `mode` is echoed so a screenshot of either run names which path produced it.
    """
    return f"""<!doctype html>
<html lang="en"> <head><meta charset="utf-8"><title>NOA embed render gate probe</title> <style>
 body {{ font: 13px/1.5 ui-monospace, monospace; margin: 12px; }}
 .row {{ margin: 4px 0; }} .k {{ color: #666; }} b {{ font-weight: 600; }}
</style></head> <body> <div id="probe"
     data-mode="{mode}" data-action-request-id="{action_request_id}" data-origin="pending"
     data-cookie-visible="pending" data-me="pending" data-decide="pending">
  <div class="row"><span class="k">mode</span> <b>{mode}</b></div>
  <div class="row"><span class="k">action_request_id</span> <b>{action_request_id}</b></div>
  <div class="row"><span class="k">origin</span> <b id="v-origin">…</b></div>
  <div class="row"><span class="k">document.cookie</span> <b id="v-cookie">…</b></div>
  <div class="row"><span class="k">GET /auth/me</span> <b id="v-me">…</b></div>
  <div class="row"><span class="k">POST /embed-probe/decide</span> <b id="v-decide">…</b></div>
</div>
<script>
(async () => {{
  const el = document.getElementById('probe');
  const set = (key, node, value) => {{
    el.dataset[key] = value; document.getElementById(node).textContent = value;
  }};

  set('origin', 'v-origin', String(window.location.origin));

  // `document.cookie` THROWS in an opaque-origin document, which is exactly what the // negative
  control is. Guarded so the control still reports the rest of its state instead // of dying on line
  one and leaving every field reading "pending". try {{
    set('cookieVisible', 'v-cookie', document.cookie === '' ? '(empty)' : document.cookie);
  }} catch (err) {{
    set('cookieVisible', 'v-cookie', 'threw: ' + err);
  }}

  // Absolute against this document's own origin. On the srcDoc path the origin is opaque,
  // so a relative URL has nothing to resolve against and the fetch fails outright — which
  // is itself the answer, recorded rather than thrown.
  const base = '{EMBED_ORIGIN}';

  try {{
    const res = await fetch(base + '/auth/me', {{ credentials: 'include' }});
    const body = await res.text();
    set('me', 'v-me', res.status + ' ' + body.slice(0, 200));
  }} catch (err) {{
    set('me', 'v-me', 'threw: ' + err);
  }}

  try {{
    const res = await fetch(base + '/embed-probe/decide', {{
      method: 'POST',
      credentials: 'include',
      headers: {{ 'content-type': 'application/json' }},
      body: JSON.stringify({{ action_request_id: '{action_request_id}' }}),
    }});
    const body = await res.text();
    set('decide', 'v-decide', res.status + ' ' + body.slice(0, 200));
  }} catch (err) {{
    set('decide', 'v-decide', 'threw: ' + err);
  }}
}})(); </script> </body> </html>
"""


def _register_probe_tools(server: FastMCP) -> None:
    """Three tools: the shape under test, the control that proves the test discriminates,
    and item (f)'s notification emitter."""

    @server.tool(
        name=TOOL_URI_LIST,
        description=(
            "Return a NOA approval surface as a `text/uri-list` UI resource. "
            "Harness tool for the render gate."
        ),
    )
    async def embed_probe_uri_list() -> ToolResult:
        """Path A: `ui://` + `text/uri-list` — mcp-ui renders an iframe `src`.

        The plain URL rides in the text block as well. That is not decoration: the link-out is
        permanent, both as the gate-fail path and as the iframe-load-failure path, so
        the harness returns the shape the product will return rather than a stripped one.
        """
        action_request_id = str(uuid.uuid4())
        url = f"{EMBED_ORIGIN}/embed-probe/frame?id={action_request_id}&mode=uri-list"
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        "Approval required. Decide here (this link works in any browser "
                        f"tab): {url}"
                    ),
                ),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl(f"ui://noa/approval/{action_request_id}"),
                        mimeType="text/uri-list",
                        text=url,
                    ),
                ),
            ]
        )

    @server.tool(
        name=TOOL_HTML,
        description=(
            "Return the same NOA approval surface as an inline `text/html` UI resource. "
            "Negative control for the render gate."
        ),
    )
    async def embed_probe_html() -> ToolResult:
        """Control path: `text/html` → `srcDoc` → opaque origin → the cookie cannot ride.

        Its job is to fail. Without it, a green result on the `text/uri-list` run proves only
        that the probe returns 200 somewhere — the same tautology a compare that never separates
        describes one axis over.
        """
        action_request_id = str(uuid.uuid4())
        return ToolResult(
            content=[
                TextContent(type="text", text="Negative control: inline HTML resource."),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl(f"ui://noa/approval-html/{action_request_id}"),
                        mimeType="text/html",
                        text=_frame_html(action_request_id=action_request_id, mode="srcdoc"),
                    ),
                ),
            ]
        )

    @server.tool(
        name=TOOL_NOTIFY,
        description=(
            "Emit `notifications/tools/list_changed` on this MCP session. "
            "Harness tool for item (f)."
        ),
    )
    async def embed_probe_notify_tools_changed(ctx: Context) -> str:
        """Item (f): fire the notification and let the request log answer.

        Emitted from inside a tool call because that is the only place with a live session to
        emit it on. What is being measured is not this call — it is whether a `tools/list`
        request arrives afterwards, which is read off NOA's access log, and whether LibreChat
        logs an unhandled-notification error rather than quietly ignoring it. A client that
        ignores this is a client whose cached catalog can name a revoked tool; a stale catalog
        is no hole — the execution-time permission re-check backstops it — so the answer changes
        the list-changed emitter's design, not its safety.
        """
        await ctx.session.send_tool_list_changed()
        return "sent notifications/tools/list_changed"


class JsonRpcMethodLog:
    """Append every JSON-RPC method the client sends, with a timestamp.

    Item (f) is a question about what LibreChat *does*, and "did a `tools/list` arrive after
    the notification" is not answerable from an access log that only records `POST /`. This
    writes the method name, so the probe reads a fact instead of inferring one.
    """

    def __init__(self, app: Any, *, log_path: str) -> None:
        self._app = app
        self._log_path = log_path

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self._app(scope, receive, send)
            return

        chunks: list[bytes] = []

        async def recording_receive() -> Any:
            message = await receive()
            if message["type"] == "http.request":
                chunks.append(message.get("body", b""))
            return message

        try:
            await self._app(scope, recording_receive, send)
        finally:
            self._record(b"".join(chunks))

    def _record(self, body: bytes) -> None:
        method = "(unparsed)"
        try:
            payload = json.loads(body or b"{}")
            if isinstance(payload, dict):
                method = str(payload.get("method", "(no method)"))
            elif isinstance(payload, list) and payload:
                method = ",".join(str(item.get("method")) for item in payload)
        except (json.JSONDecodeError, AttributeError):
            pass

        with open(self._log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f} {method}\n")


def build_probe_mcp_app(*, auth: AuthProvider) -> StarletteWithLifespan:
    """The probe MCP server, authenticated exactly like production."""
    server = FastMCP("NOA embed render gate probe", auth=auth, mask_error_details=True)
    _register_probe_tools(server)
    app = server.http_app(
        path=PROBE_APP_PATH,
        middleware=[Middleware(McpAuthErrorMiddleware, mcp_path=PROBE_APP_PATH)],
    )
    app.add_middleware(JsonRpcMethodLog, log_path=JSONRPC_LOG_PATH)
    return app


def create_probe_app() -> FastAPI:
    """NOA's app (real `/mcp`, real `/auth`) plus the probe surfaces.

    Mirrors `noa_api.main.create_app` rather than calling it: a second streamable-HTTP mount
    needs its lifespan combined in at construction time, and `create_app` has already fixed
    its lifespan by the time it returns. Duplicated deliberately and only here.
    """
    runtime = build_runtime(get_settings())

    auth_context = build_mcp_auth_context(
        session_factory=runtime.session_factory,
        directory=runtime.ldap_service,
        settings=runtime.settings,
    )
    # The tool context comes off the runtime rather than being rebuilt here. `build_runtime`
    # already wires one, and every setting it takes is a keyword `build_mcp_tool_context`
    # requires; a second call site is a second thing to update whenever that list grows, which
    # is exactly how this harness stopped booting between runs.
    mcp_app = build_mcp_http_app(auth_context=auth_context, tool_context=runtime.tool_context)
    probe_app = build_probe_mcp_app(auth=NoaTokenVerifier(context=auth_context))

    app = FastAPI(
        title="NOA API (embed render gate harness)",
        lifespan=combine_lifespans(
            build_lifespan(runtime),
            mcp_app.lifespan,
            probe_app.lifespan,
        ),
    )

    install_error_handling(app)
    app.include_router(auth_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/embed-probe/frame", response_class=HTMLResponse)
    async def frame(id: str = "", mode: str = "uri-list") -> HTMLResponse:
        """The NOA-origin document LibreChat is asked to iframe.

        No authentication on the page itself, matching the real embed: the card renders and
        *then* discovers whether the session travelled, because a 401 must become
        an explicit state rather than a blank card.
        """
        return HTMLResponse(_frame_html(action_request_id=id or "(none)", mode=mode))

    @app.post("/embed-probe/decide")
    async def decide(request: Request, current_user: SessionUserDep) -> JSONResponse:
        """Stand-in for the approve POST: 200 with the identity iff the cookie arrived.

        `SessionUserDep` is production's own gate (`require_session_user`), so a 200 here
        means the same code that will authorize a real decision authorized this one.
        """
        payload: dict[str, Any]
        try:
            payload = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            payload = {}

        return JSONResponse(
            {
                "authenticated_as": current_user.email,
                "action_request_id": payload.get("action_request_id"),
                "origin_header": request.headers.get("origin"),
                "sec_fetch_site": request.headers.get("sec-fetch-site"),
                "sec_fetch_dest": request.headers.get("sec-fetch-dest"),
            }
        )

    app.mount(MCP_MOUNT_PATH, mcp_app)
    app.mount(PROBE_MOUNT_PATH, probe_app)

    return app


app = create_probe_app()


if __name__ == "__main__":  # pragma: no cover - harness entry point
    uvicorn.run(
        app,
        # The rig answers to three hostnames that all resolve to loopback, so it binds all
        # interfaces. Harness only — nothing here is a deployment.
        host="0.0.0.0",  # noqa: S104
        port=8000,
        log_level="info",
        access_log=True,
    )
