/**
 * What the card's payload blocks print, and what they leave to `/admin`.
 *
 * Its own file rather than a section of `app/approvals/[id]/card-view.tsx`, for the reason
 * `delta.ts` is not a section of `card.ts`: that component sits against this package's 450-line
 * ceiling for a `.tsx`, and this is a cohesive unit with no JSX in it — two hand-kept sets, the
 * flatten they are matched against, and the one function that applies them. Keeping it there meant
 * paying for every rule below in a file that has none of the room, which is how a rule ends up
 * shortened to fit rather than stated.
 *
 * The rules themselves are unchanged by the move, and the suite that holds them is unchanged too:
 * `app/approvals/[id]/card-view-render.test.tsx` asserts on the rendered rows, so it exercises this
 * through the component that calls it rather than through an export nothing else reads.
 */

import { formatJakarta } from '@/lib/format/jakarta-time'

/**
 * A nested payload as one row per leaf, keyed by its path.
 *
 * A nested object serialised into a single row is one line of JSON in a frame this narrow, which
 * is the least readable thing on the card and the reason the arguments block gets this treatment.
 * Lists are left whole: a firewall match list runs to twenty entries, and twenty rows would cost
 * more of the one screen this card gets than the list is worth.
 */
export function flattenValues(
  values: Record<string, unknown>,
  prefix = '',
): Record<string, unknown> {
  const flat: Record<string, unknown> = {}

  for (const [key, value] of Object.entries(values)) {
    const path = prefix ? `${prefix}.${key}` : key
    const nested =
      typeof value === 'object' && value !== null && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null

    // An empty object has no leaves, so it renders as itself rather than as no row at all.
    if (nested && Object.keys(nested).length > 0) Object.assign(flat, flattenValues(nested, path))
    else flat[path] = value
  }

  return flat
}

/**
 * The paths the before-state block does not print. Full paths, not leaf names.
 *
 * Two identifiers NOA needs and the operator does not — `server_id` is the machine's row, and
 * `api_username` the credential the preflight was read with — plus the identity fields of the
 * account record a WHM preflight carries.
 *
 * **What is hidden is identity, not the record.** This set used to name `account` whole, which
 * dropped the fields a suspend actually moves along with the six that name the account. The
 * panel then answered "who is this" under a heading promising the state the change is about, and on
 * a PENDING card — where the execution and outcome blocks do not exist yet — that left no mutable
 * state anywhere at the moment the operator decides. `suspended`, `suspendtime` and `is_locked`
 * stay; `is_locked` in particular is what blocks an unsuspend, so it is the answer to "will this
 * work" rather than a detail.
 *
 * `account.suspendreason` is hidden even though it moves with the change. WHM echoes the operator's
 * own typed NOA reason back into that field verbatim — measured against a live account read — so it
 * carries nothing a decision rests on, and it is a reason-bearing key on the API side
 * (`core/approvals/delta.py`).
 *
 * **Matching is on the full dotted path, and that is a correctness requirement rather than a
 * style.** `owner` exists twice in a WHM preflight: at the top level, where it is the reseller the
 * machine answers to and is shown, and as `account.owner`, which is the same identity repeated on
 * the record and is hidden. A leaf-name filter would kill both. Hence the flatten below happens
 * *before* the filter rather than at the call site, so the order cannot be got wrong by an edit
 * somewhere else.
 *
 * **These are admin-only by design, and the copy summary does not carry them.** They answer an
 * administrator's question and are reached through `/admin` (held by
 * `apps/api/tests/test_admin_action_request_routes.py`). That used to be the contrast with the
 * identifiers that left the provenance and execution blocks, on the grounds that those ride in the
 * copied summary instead — no longer: that summary keeps the run id alone and dropped the
 * LibreChat account and the conversation reference as this block drops its own, so for those two
 * the admin panel is now the single surface as well. Everything here stays in the database and in
 * the API's body regardless; what changes is only which surface prints it.
 *
 * **Hand-kept, and nothing binds it to the producer.** These paths are TypeScript and the nine
 * keys they filter come out of `normalize_whm_account_summary` in Python, so an identity field
 * added there renders on this card and reddens nothing here. Said rather than implied, the way
 * the tool list in `card-view-render.test.tsx` says it of itself.
 */
const BEFORE_STATE_HIDDEN = new Set([
  'server_id',
  'api_username',
  'account.user',
  'account.domain',
  'account.email',
  'account.contactemail',
  'account.owner',
  'account.suspendreason',
])

/**
 * The one before-state path carrying an epoch second rather than a number anybody reads.
 *
 * `account.suspendtime` arrives as the integer `_optional_epoch` normalises it to
 * (`core/integrations/whm/accounts.py`), and `1789109810` answers "when was this suspended" with a
 * value an operator has to go and convert.
 *
 * **The zone is named on the value, because nothing around it names one.** Every other time on the
 * card is relative with the instant in a `title`, so this is the one stamp there a screenshot could
 * carry into a ticket bare — and a bare stamp may leave the frame only under a heading stating the
 * zone, which is the rule `lib/format/jakarta-time.ts` is built around and which the before-state
 * block has no heading to satisfy. `WIB` is the spelling the copied summary's own heading uses.
 *
 * **Hand-kept, and nothing binds it to the producer**, exactly as the set above is: a second epoch
 * key added on the Python side renders on the card as an integer and reddens nothing here.
 */
const BEFORE_STATE_EPOCH = 'account.suspendtime'

/** The before-state payload as the rows the card prints: flattened, filtered, epoch made readable. */
export function beforeStateRows(values: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(flattenValues(values))
      .filter(([path]) => !BEFORE_STATE_HIDDEN.has(path))
      // Anything that is not a finite number is left exactly as it arrived: `new Date(NaN)` throws
      // on the way to an ISO string, and a card that throws is a blank frame.
      .map(([path, value]): [string, unknown] =>
        path === BEFORE_STATE_EPOCH && typeof value === 'number' && Number.isFinite(value)
          ? [path, `${formatJakarta(new Date(value * 1000).toISOString())} WIB`]
          : [path, value],
      ),
  )
}
