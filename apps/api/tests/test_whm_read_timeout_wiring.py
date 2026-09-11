"""The configured WHM read deadline, from the environment to the socket.

`test_whm_client.py` proves the client and both factories honour the number they are given, and
that a real socket is abandoned at it. What neither can see is the step between: `create_app` is
the single `get_settings()` caller, so a deadline it forgets to pass leaves the setting
documented, quotable and inert, with every tool waiting the client's own default instead.
Nothing else in the suite fails for that — the wiring is one keyword argument, and a missing one
falls back to a default of the same shape.

The configured value here is deliberately not the `Settings` default, for the reason the pending
TTL's wiring assertion gives one file over: against 120 the compare could not separate a wired
setting from a hardcoded literal.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from support.secrets import build_cipher
from support.whm import FakeWHMServer


async def test_the_mount_hands_the_whm_factory_the_configured_read_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noa_api import main
    from noa_api.mcp_tools.context import build_mcp_tool_context
    from support.auth import build_settings

    configured = 37.5
    captured: dict[str, Any] = {}

    def capturing_builder(**kwargs: Any) -> Any:
        captured.update(kwargs)
        context = build_mcp_tool_context(**kwargs)
        captured["context"] = context
        return context

    monkeypatch.setattr(
        main, "get_settings", lambda: build_settings(whm_read_timeout_seconds=configured)
    )
    monkeypatch.setattr(main, "build_mcp_tool_context", capturing_builder)
    main.create_app()

    assert captured["whm_read_timeout_seconds"] == configured

    # And it is bound onto the seam rather than merely received. A tool asks this factory for a
    # client and passes no deadline of its own, so the binding is the only thing that can carry
    # the number to the request — a context that took the value and left the default factory in
    # place would satisfy the assertion above and change nothing on the wire.
    cipher = build_cipher()
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(status_code=200, json={"metadata": {"result": 1}}, request=request)

    client = captured["context"].whm_client_factory(
        FakeWHMServer(api_token=cipher.encrypt_text("API_TOKEN")),
        cipher=cipher,
        # The production factory's own test seam, taken the way `support.servers` takes it: the
        # real client, the real cipher and the real decrypt site stay in the path, and only the
        # socket is doubled.
        transport=httpx.MockTransport(handler),
    )
    await client.applist()

    assert seen["timeout"] == {"connect": 10.0, "read": configured, "write": 30.0, "pool": 10.0}
