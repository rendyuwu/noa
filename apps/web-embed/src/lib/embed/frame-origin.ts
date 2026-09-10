import { resolveFrameAncestor } from '../../../config/framing'

/**
 * The origin this document posts its height to — resolved, never `"*"` (§T.45, V41, V66).
 *
 * **One resolver, reused.** `config/framing.ts::resolveFrameAncestor` is the single place that turns
 * `NOA_LIBRECHAT_ORIGIN` into an origin string, and it is the same value the `frame-ancestors`
 * header carries. A second copy of that rule for the sake of a `postMessage` target would be two
 * descriptions of one deployment, free to disagree the moment either is edited. `"*"` is not an
 * option at all: the message is a fact about this operator's card, and a wildcard target hands it to
 * whatever document happens to be framing us.
 *
 * **There is a time problem, and it is why this wrapper exists.** `next.config.ts` says it in as
 * many words: `output: 'standalone'` never executes that config at runtime, so the framing origin is
 * baked at `next build` — a runtime variable cannot widen the allowlist and cannot change it either.
 * A request-time read of the same variable can therefore disagree with the baked one, and two
 * failure modes follow:
 *
 * - **Build-set, runtime-unset.** `resolveFrameAncestor` falls back to `DEFAULT_LIBRECHAT_ORIGIN`,
 *   so the CSP is right and the frame renders, while every message goes to an origin the parent does
 *   not have and the browser drops it. No exception, no console error: the frame simply never grows,
 *   which is indistinguishable from a host that stopped listening. Asserted rather than argued in
 *   `tests/next-config-headers.test.ts`, where the two values are compared against each other.
 * - **Malformed value.** `resolveFrameAncestor` throws on purpose — a config error should stop a
 *   deploy, not surface later as a header that says something nobody meant. That is correct at build
 *   time and wrong at request time: inside the card's server component it would be a 500 on
 *   `/approvals/[id]`, so the approve path would die for a cosmetic feature's misconfiguration.
 *
 * Hence the wrap. `null` means "do not size the frame", which the sizer already treats as a
 * supported state: internal scrolling stays, the link-out and the printed address beside it stay
 *, and the surface is what it is today.
 */
export function resolveFrameTargetOrigin(env: Record<string, string | undefined>): string | null {
  try {
    return resolveFrameAncestor(env)
  } catch {
    // Deliberately swallowed, and only here: the same throw at build time is the gate that stops
    // the deploy. What must not happen is an operator losing the Approve button over it.
    return null
  }
}
