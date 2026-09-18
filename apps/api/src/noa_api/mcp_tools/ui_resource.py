"""The two surfaces NOA renders inside LibreChat's frame, shaped once.

Two tool results carry a NOA-origin document: a CHANGE's approval card and a large
READ's table. The summary-plus-URL rule says the table's mechanism is *the same* one the
approval card's path names — path A, the iframe UI resource — and not a second one, so the
three fields that make an iframe render at all are stated here rather than twice.

**All three are load-bearing together** — measured live in Chromium, pinned to LibreChat commit
`45cc53c4`:

- the `ui://` scheme is what LibreChat's parser classifies on. Without it the resource
  arrives as an ordinary attachment and never renders (`packages/api/src/mcp/parsers.ts:183`);
- `text/uri-list` is what mcp-ui maps to `externalUrl` -> `iframeRenderMode: 'src'`, which is
  what puts the document on NOA's own origin with `allow-same-origin` — `text/html` renders
  `srcDoc` on an opaque origin, where the session cookie cannot follow;
- the body is the URL, because that is what a uri-list *is*.

`embed_url` is the join both surfaces make onto `NOA_EMBED_BASE_URL`. It strips a trailing
slash even though `core.config` already does, because it is also called with literals in
tests — and one doubled slash is the kind of thing that only shows up in front of an operator.
"""

from __future__ import annotations

from typing import Final

from mcp.types import EmbeddedResource, TextResourceContents
from pydantic import AnyUrl

# The half of the pair that picks the render *mode* — see the module docstring.
UI_RESOURCE_MIME_TYPE: Final = "text/uri-list"


def embed_url(*, embed_base_url: str, path: str, identifier: str) -> str:
    """One embed address: base + route + the id or token that names the row.

    The identifier and nothing else follows the route. Everything in this string ends up in a
    tool result that persists in LibreChat's MongoDB, so authorisation to read what is
    behind it is the `noa_session` cookie plus the requester-match evaluated at fetch time —
    the URL is a name, not a key.
    """
    return f"{embed_base_url.rstrip('/')}{path}/{identifier}"


def build_ui_resource(*, uri: str, url: str) -> EmbeddedResource:
    """The iframe half of a tool result, in the shape the render gate measured."""
    return EmbeddedResource(
        type="resource",
        resource=TextResourceContents(
            uri=AnyUrl(uri),
            mimeType=UI_RESOURCE_MIME_TYPE,
            text=url,
        ),
    )
