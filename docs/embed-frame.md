# The frame-sizing contract

How a NOA embed surface asks the host that frames it for a taller frame.

This is not the LibreChat configuration reference — that is
`docs/integrations/librechat.md`, which covers the MCP server entry, the SSRF allowlist, the
per-operator token and the example agent prompt. This file answers one narrower question: what NOA
posts out of the iframe, where it posts it, and which of the bounds on that number are ours to
hold.

It exists because the contract had no home. It lives in the header comments of three source files
and in a spike note, and the as-built record that used to carry it was retired with the rest of the
spec artifacts. A message shape that is only described where it is implemented is one that is
re-derived by whoever next reads the implementation.

## The message

```js
{ type: 'ui-size-change', payload: { height } }
```

`apps/web-embed/src/lib/embed/frame-size.ts` is the only place it is built (`frameSizeMessage`),
and `apps/web-embed/src/components/frame-sizer.tsx` is the only place it is sent. One component
serves both surfaces — the approval card and the large-result table — because the difference
between them is a policy object and not a second message.

`ui-size-change` is `@mcp-ui/client`'s own name for the message, not NOA's. Renaming the constant
here would be a no-op that breaks the sizing.

### Height only. Never width

The payload carries no `width` key at all, and this is deliberate rather than an omission. A
payload that omits it leaves the host's own `width: 100%` untouched; one that carried a width would
fight the host for the layout of its message column, which is the host's to decide and not a framed
document's.

### Posted to a resolved origin. Never `"*"`

`apps/web-embed/src/lib/embed/frame-origin.ts` resolves the target, and it does so through
`resolveFrameAncestor` in `apps/web-embed/config/framing.ts` — the same function that produces the `frame-ancestors`
entry in the Content-Security-Policy header, reading `NOA_LIBRECHAT_ORIGIN` and falling back to
`https://chat.noa.internal`. One resolver for both, because two copies of that rule would be two
descriptions of one deployment, free to disagree the moment either is edited.

`"*"` is not an option. The message is a fact about one operator's card, and a wildcard target
hands it to whatever document happens to be framing us.

The resolver answers `null` when it cannot produce a trustworthy origin, and `null` is a supported
state rather than a failure: nothing is posted, the surface keeps its own internal scrolling, and
what an operator loses is a taller frame rather than the surface. The wrapper exists because
`resolveFrameAncestor` throws on a malformed value — correct at build time, where it should stop a
deploy, and wrong at request time, where it would turn a cosmetic misconfiguration into a 500 on
the approval route.

## Every bound on the number is ours

Measured against the pinned client, `@mcp-ui/client` 5.7.0 under LibreChat
`45cc53c40b47645b887c3bb996168e06aaa83f4c` (`docs/spikes/librechat-embed-render-gate.md`, evidence
under `spikes/librechat-embed-render-gate/evidence/`): the host applies the posted height
**verbatim**, and a request for 20000px was honoured. There is no host-side clamp. A bad number is
this app's bug, and the host will not save us from it.

So the rails live here, each with its reasoning beside it in `frame-size.ts`:

| bound | value | what it is for |
|---|---|---|
| floor | 160px | a measurement taken before layout exists reads as near zero; 160 is the order of the box the host opens with |
| card ceiling | 4800px | a rail, not an operating point — the card asks for its content height, and this stops a pathological payload |
| table ceiling | 720px | an operating point — a 438-row listing is on the order of 15,000px, and the rows keep scrolling inside the frame |
| epsilon | 8px | smaller than one line box, so it filters device-pixel rounding and nothing else |
| post budget | 12 per mount | a backstop behind the two controls that actually prevent a loop |

Two rules matter more than the numbers. **A measurement above the ceiling posts the ceiling, never
nothing** — returning nothing would leave a pathological payload sitting in the host's opening box,
which is the defect the module was written to fix. And **the height is monotonic within one
mount**: a decrease is never posted, because the oscillation this could have shipped is
scrollbar-mediated and a shorter frame is the first step of that loop. The other half of that fix
is `scrollbar-gutter: stable` in the surfaces' stylesheets, which removes the width term from the
loop rather than damping it.

Exhausting the post budget is an observable state on the page (`data-noa-frame-size`), not a silent
stall: a frame that quietly stopped asking is indistinguishable from a host that quietly stopped
listening, and those two send an operator to different people.

## Re-verify on a LibreChat bump

The pin above is the premise for everything on this page, and for the sandbox flags the surfaces
are built against. `spikes/librechat-embed-render-gate/verify_librechat_pin.sh` is the check;
`docs/spikes/librechat-embed-render-gate.md` records what each of its assertions is for and what it
cannot see.

If a bump stops honouring the message, nothing here breaks loudly — both surfaces still scroll
themselves and the frame simply never grows. That is the degraded path working as designed, and it
is also why the bump has to be checked deliberately rather than noticed.
