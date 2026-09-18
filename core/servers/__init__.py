"""Server inventory: reads, reference resolution, and the admin write path.

`core/integrations/<system>/` knows how to *talk to* a server. This package knows how to
*find* one: which rows exist, and which row an operator meant when they typed a word.
They are kept apart deliberately — the integration modules take a `Protocol`-shaped row
(`WHMServerSecretLike`) precisely so they stay independent of SQLAlchemy and of the session
that loaded it, and putting a `select()` in there would undo that.

Landed: WHM, PMG, Proxmox.

**The question this docstring carried is answered.** The first port noted that `noa-old` had one
`server_ref.py` per system and that the third would show "whether that difference is two
parameters or a third shape". Proxmox arrived and is WHM's shape exactly — a hostname
parsed out of `base_url`, a candidate described by id/name/`base_url` — so it was two
parameters: how a row yields its host, and what a `choices` entry carries. The policy moved to
`core.servers.reference` (id, then name, then host; any tie is `choices`; a well-formed id stops
rather than falling through) and the three per-system modules shrank to their row's own facts.
Codes and messages are unchanged, which is what the two existing resolver test files hold.

**The write half landed with the server admin API**, in three modules kept apart from the three read
ones on purpose: the MCP tool path holds a read repository to resolve a reference, and it must not
hold an object that can delete a server (`admin_repository`). `admin_service` owns the rules — the
case-insensitive name check, the one encryption site, an audit event per mutation, and the commit
the flush-is-not-persisted rule requires. `validation` owns `POST …/validate`, which is separate
again because it opens a socket to somebody else's host and may write exactly one column: the
host-key pin. `naming` and `errors` are shared by all of them.
"""
