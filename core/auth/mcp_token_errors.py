"""MCP token error taxonomy.

A third taxonomy alongside `core.auth.errors` (authentication) and
`core.auth.authorization_errors` (RBAC), and separate from both on purpose. Those two
answer "who are you?" and "may you do this?"; these answer "does this credential exist?"
— a question about a row in `mcp_tokens`, not about the caller's identity or role.

Folding them into `authorization_errors` would also have mixed MCP-credential codes into
a module whose `error_code` strings are lifted verbatim from `noa-old`'s admin API, where
the admin panel, ported (not imported) from `noa-old`, already branches on them.

`noa_api.api.errors` maps each class to a status, and a test walks the subclass tree so a
class added later without a mapping fails there instead of returning 503.

Statuses and why:

- 404 `mcp_token_not_found` — no such token *for this user*. Deliberately the same answer
  for a token that never existed and one belonging to a colleague: the lookup is scoped by
  `user_id` in the WHERE clause, so a caller cannot use the 404/403 split to discover
  which ids are real (the requester-match existence-never-leaks principle, applied to
  tokens).
- 400 `invalid_token_label` — the label is longer than the column holds. Caught here so it
  is a bad request rather than a database error surfacing as a 500.

The token-scope and envelope assertions throughout: no message, and no `detail`, carries a
token plaintext, a prefix, or a hash.
"""

from __future__ import annotations

from core.errors import NoaError


class McpTokenError(NoaError):
    """Base for every MCP token failure.

    Mapped to 400 rather than left to the 503 fallback: an unclassified token failure is a
    problem with the request, not with NOA's infrastructure. A test asserts every subclass
    is mapped explicitly, so reaching this entry means a new class arrived without a
    decision.
    """

    error_code: str = "mcp_token_error"
    message: str = "That MCP token request could not be completed."
    status_code = 400


class McpTokenNotFoundError(McpTokenError):
    """No `mcp_tokens` row with that id for that user.

    Raised by revoke. One shape for "never existed" and "belongs to someone else", so the
    response is not an enumeration oracle.
    """

    error_code: str = "mcp_token_not_found"
    message: str = "That MCP token no longer exists."
    # 404 for a token that is absent *or* another user's: the lookup is scoped by user id,
    # so the two cases answer identically and the response is not an enumeration oracle
    # (the token-scope assertion, and the requester-match principle).
    status_code = 404


class InvalidTokenLabelError(McpTokenError):
    """Label exceeds what `mcp_tokens.label` holds (`String(255)` in the schema).

    A blank label is not an error — it normalizes to `None`, because a token with no name
    is a legitimate thing to mint and refusing it would add a required field the schema
    does not have.
    """

    error_code: str = "invalid_token_label"
    message: str = "A token label may be at most 255 characters."
    # The label is longer than the column holds — a malformed request, not a server fault.
    status_code = 400
