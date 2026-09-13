# The frame-sizing contract

How NOA embed surface ask host for taller frame.

This not LibreChat config reference — that `docs/integrations/librechat.md`, which cover MCP server entry, SSRF allowlist, per-operator token, example agent prompt. This file answer one narrow question: what NOA post out of iframe, where it post, which bounds ours to hold.

It exist because contract have no home. It live in header comments of three source files and in spike note, and as-built record that carry it got retired with rest of spec artifacts. Message shape only described where implemented = shape re-derived by next reader.

## The message

```js
{ type: 'ui-size-change', payload: { height } }
```

`apps/web-embed/src/lib/embed/frame-size.ts` only place it built (`frameSizeMessage`), and `apps/web-embed/src/components/frame-sizer.tsx` only place it sent. One component serve both surfaces — approval card and large-result table — because difference between them be policy object, not second message.

`ui-size-change` be `@mcp-ui/client` own name, not NOA name. Rename constant here = no-op that break sizing.

### Height only. Never width

Payload carry no `width` key at all. Deliberate, not omission. Payload that omit it leave host own `width: 100%` alone; payload that carry width fight host for layout of its message column — host decide that, not framed doc.

### Posted to a resolved origin. Never `"*"`

`apps/web-embed/src/lib/embed/frame-origin.ts` resolve target. Parsing shared with `frame-ancestors` entry in Content-Security-Policy header — `normalizeFrameOrigin` in `apps/web-embed/config/framing.ts` be one place origin validated and normalised, because two copies of rule = two descriptions of one deployment, free to disagree moment either edited.

`"*"` not option. Message be fact about one operator card, and wildcard target hand it to whatever doc happen to frame us.

### One validator, two variables, and two different answers to "unset"

Two consumers read two different env variables. Both build-time inputs:

| Consumer | Variable | Unset or blank |
|---|---|---|
| `frame-ancestors` header | `NOA_LIBRECHAT_ORIGIN` | fall back to `https://chat.noa.internal` — header never omitted, never widened |
| `postMessage` target | `NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN` | `null` — nothing posted, page say so |

`NEXT_PUBLIC_` prefix not about browser. Nothing in this app call API from browser, and this origin be public chat address anyway. Prefix be what make Next compile value into output, only way value reach standalone server: `output:
'standalone'` never re-run `next.config.ts`, so header origin baked at `next build`, and message target read at runtime could not agree with it. So both names must pass to `docker build`, and **built image not interchangeable across environments** — move LibreChat mean rebuild, already true of header alone.

Two names not one because header must not read public one. Every build arg, compose file, pipeline written before twin exist set only private name; header wired to public name would quietly emit dev default in all of them while looking configured.

Remaining half-configured case — private name set, public name not — be why two sides answer "unset" different. Fallback on message side look configured but not be: browser drop every message on origin mismatch, no exception, no console entry, and frame that never grow look same as host that stop listening. `null` instead render `data-noa-frame-size="no-target-origin"` on page, where it findable.

`null` be supported state, not failure, in every case that make it: nothing posted, surface keep own internal scrolling, operator lose taller frame not surface. Wrapper around validator exist because malformed value throw — right at build time, where it should stop deploy; wrong at request time, where it turn cosmetic misconfig into 500 on approval route.

### The check that the value survives a build

`pnpm test:inlining` (`apps/web-embed/tests/frame-origin-inlining.mjs`) build with probe origin, start standalone server with **both** framing variables removed from env, and assert served card carry `data-noa-frame-size="measuring"` — state reachable only when target origin resolved, so only when value compiled in. Not part of `pnpm test`: it run real build and boot real server. Run it after touch `config/framing.ts`, `src/lib/embed/frame-origin.ts`, or this app Dockerfile.

Unit test cannot make this claim. In-process read be dynamic either way, so spec that set variable pass against build that baked nothing. Measured on Next 16.3.0 with Turbopack: point resolver at private `NOA_LIBRECHAT_ORIGIN` make check fail — that be mistake it exist to catch. Rewrite read as `process.env[SOME_CONST]` do **not** make it fail — Turbopack constant-fold statically resolvable key — so this check guard variable prefix, not spelling of read. Accessor still written as literal member expression, because that what Next own guide guarantee and folding be observation about one bundler version.

## Every bound on the number is ours

Measured against pinned client, `@mcp-ui/client` 5.7.0 under LibreChat `45cc53c40b47645b887c3bb996168e06aaa83f4c` (`docs/spikes/librechat-embed-render-gate.md`, evidence under `spikes/librechat-embed-render-gate/evidence/`): host apply posted height **verbatim**, and request for 20000px got honoured. No host-side clamp. Bad number = this app bug. Host not save us.

So rails live here, each with reasoning beside it in `frame-size.ts`:

| bound | value | what it is for |
|---|---|---|
| floor | 160px | measurement taken before layout exist read near zero; 160 be order of box host open with |
| card ceiling | 4800px | rail, not operating point — card ask for content height, this stop pathological payload |
| table ceiling | 720px | operating point — 438-row listing be order of 15,000px, rows keep scrolling inside frame |
| epsilon | 8px | smaller than one line box, so it filter device-pixel rounding and nothing else |
| post budget | 12 per mount | backstop behind two controls that actually stop loop |

Two rules matter more than numbers. **Measurement above ceiling post ceiling, never nothing** — post nothing leave pathological payload sitting in host opening box, the defect module written to fix. And **height monotonic within one mount**: decrease never posted, because oscillation this could have shipped be scrollbar-mediated and shorter frame be first step of that loop. Other half of fix be `scrollbar-gutter: stable` in surfaces stylesheets, which remove width term from loop instead of damping it.

Exhaust post budget be observable state on page (`data-noa-frame-size`), not silent stall: frame that quietly stop asking look same as host that quietly stop listening, and those two send operator to different people.

## Re-verify on a LibreChat bump

Pin above be premise for everything on this page, and for sandbox flags surfaces built against. `spikes/librechat-embed-render-gate/verify_librechat_pin.sh` be check; `docs/spikes/librechat-embed-render-gate.md` record what each assertion for and what it cannot see.

If bump stop honouring message, nothing here break loud — both surfaces still scroll themselves and frame simply never grow. That be degraded path working as designed, and also why bump must be checked on purpose, not noticed.