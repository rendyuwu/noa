# apps/web-embed

Next.js 16 app served on the NOA origin. Hosts the approval card and the large-result table surface.

Not scaffolded yet — see `SPEC.md` §T.40 (scaffold) and §T.41–T.46, §T.56 (routes).

Blocked on §T.59. That gate pins a LibreChat commit and decides whether the approval surface renders
as an iframe (path A) or as a link-out to a top-level tab (path B). Starting embed work before the
gate resolves means building against an unverified render path (C21).

Routes, once built:

| Route | Purpose |
|---|---|
| `/approvals/[id]` | Approval card. The reason input lives here and nowhere else (C8, V15). |
| `/action-requests/{id}` | Confirmation detail + execution status polling |
| `/tables/[token]` | Large READ result table (V64) |
| `/healthz` | Liveness |

Sends `Content-Security-Policy: frame-ancestors https://chat.noa.internal` (V41). Has no login page,
no LDAP form, no credential handling — a 401 renders an explicit "cannot authenticate here" state,
never a blank card with a live Approve button (V38, V42).
