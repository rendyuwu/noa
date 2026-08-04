# apps/admin-web

Next.js 16 + BIGSU admin panel. Users, roles, tokens, servers (WHM/Proxmox/PMG), audit.

Not scaffolded yet — see `SPEC.md` §T.47 (scaffold, `.npmrc` for the BIGSU registry) and §T.48
(port pages from `noa-old/apps/web-bigsu/src/app/(protected)/admin/`).

Independent package: own `package.json`, own lockfile, own CI, own deploy artifact. Shares no
source or deps with `apps/web-embed`.

Sends `Content-Security-Policy: frame-ancestors 'none'` — this app is never framed (V41).
