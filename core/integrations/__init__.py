"""Per-system integration layers (C12, C13, DECISIONS §4.1).

One subpackage per external system NOA drives: `whm`, `proxmox`, `pmg`.

They live in `core/` and not in `apps/api/` because the integration layer is joint property.
DECISIONS §4.1 makes that the load-bearing argument for the monorepo: validating a server from
the admin panel means an SSH connect, a fingerprint capture and a TOFU refresh — the same code
the MCP tools execute through. Splitting the deployables would mean duplicating it or packaging
it internally.

Each subpackage owns its transports and its parsing, and takes its dependencies as arguments —
a `SecretCipher` for credentials at rest, a resolved `SSHConnectionConfig` for escalation. None
of them reads configuration or opens a database session.
"""
