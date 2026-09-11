import { normalizeFrameOrigin } from '../../../config/framing'

/**
 * The variable this module reads, and the `NEXT_PUBLIC_` prefix is what makes it work.
 *
 * The prefix, not the spelling of the read, is the load-bearing part — MEASURED, because the
 * obvious guess is wrong in both directions and a comment asserting the guess would be worse than
 * none. What was measured, on Next 16.3.0 with Turbopack, by building with a probe origin and
 * serving the result with the variable removed from the runtime environment:
 *
 * - Point this at the private `NOA_LIBRECHAT_ORIGIN` and the value is **not** baked. It stays a
 *   runtime read, answers `undefined` in the deployed container, and the card ships with no target
 *   origin at all. That is the failure this name exists to avoid.
 * - A variable-keyed `process.env[SOME_CONST]` **was** inlined, and so was
 *   `const env = process.env` followed by `env[SOME_CONST]`: Turbopack constant-folds a key it can
 *   resolve statically. Next's own guide, vendored at
 *   `node_modules/next/dist/docs/01-app/02-guides/environment-variables.md`, still says *"dynamic
 *   lookups will not be inlined"*, and the accessor below is written as a literal
 *   `process.env.NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN` to stay inside what that guide guarantees rather
 *   than inside what one bundler version happens to fold. The folding is an observation about
 *   today's toolchain, not a property to depend on.
 *
 * So this constant names the key for anything that has to set it — a test, a harness — and the
 * accessor spells the same name out. Nothing binds the two, which is why they are three lines
 * apart; the end-to-end check named below is what fails if they ever diverge in a way that matters.
 */
export const PUBLIC_LIBRECHAT_ORIGIN_ENV_VAR = 'NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN'

/**
 * The origin this document posts its height to — resolved, never `"*"`, `null` when unconfigured.
 *
 * **One validator, two variables.** `config/framing.ts::normalizeFrameOrigin` is the single place
 * that parses an origin, and the `frame-ancestors` header goes through it too. What differs is
 * which variable each side reads, and that difference is deliberate rather than an oversight:
 *
 * - the header reads `NOA_LIBRECHAT_ORIGIN`, which is server-side and which nothing may set at
 *   runtime, because `output: 'standalone'` never re-executes `next.config.ts`;
 * - this reads `NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN`, the twin Next bakes into the compiled output at
 *   `next build`.
 *
 * Both are build-time inputs naming one deployment's chat origin, and the image is therefore not
 * interchangeable across environments — moving LibreChat means rebuilding, which was already true
 * of the header alone. `"*"` is not an option on either side: the message is a fact about this
 * operator's card, and a wildcard target hands it to whatever document happens to be framing us.
 *
 * **Why two variables and not one.** A single public name would have to carry the header as well,
 * and then a build or a compose file that set only the private name — every one written before this
 * twin existed — would emit the development default while reading as configured. That failure is
 * silent by construction, so the safer shape is the one where a half-configured build is visible.
 *
 * **`null` is a supported state, not a failure**, and it is what makes a misconfiguration
 * observable. Unset, blank, or malformed all land here, `FrameSizer` posts nothing and renders
 * `data-noa-frame-size="no-target-origin"` on the page, and both surfaces still scroll themselves:
 * what an operator loses is a taller frame, not the card. The alternative — falling back to the
 * pinned development origin the way the header does — looks configured and is not: the browser
 * drops every message for an origin mismatch, with no exception and no console entry, and a frame
 * that never grows is indistinguishable from a host that stopped listening.
 *
 * The malformed case is the one worth naming separately. `normalizeFrameOrigin` throws on purpose
 * so a bad value stops a build, and that is correct at build time and wrong at request time: read
 * inside the card's server component, the same throw is a 500 on `/approvals/[id]`, so the approve
 * path would die for a cosmetic feature's misconfiguration. Hence the catch here, and only here —
 * the header path lets that throw propagate, which is what stops the deploy.
 *
 * Whether the inlining actually happened is not something a unit test can answer: one passing a
 * literal env object stays green against a build that inlined nothing. That claim is made
 * end-to-end by `tests/frame-origin-inlining.mjs`, which builds with a probe origin, starts the
 * standalone server with both framing variables unset, and asserts the served HTML reached
 * `data-noa-frame-size="measuring"` — a state only a non-null answer here can produce.
 */
export function resolveFrameTargetOrigin(): string | null {
  try {
    // Spelled out, not `process.env[PUBLIC_LIBRECHAT_ORIGIN_ENV_VAR]`: see that constant.
    return normalizeFrameOrigin(
      process.env.NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN,
      PUBLIC_LIBRECHAT_ORIGIN_ENV_VAR,
    )
  } catch {
    // Deliberately swallowed, and only here: the same throw at build time is the gate that stops
    // the deploy. What must not happen is an operator losing the Approve button over it.
    return null
  }
}
