/**
 * What a CHANGE runner measured, as the receipt carries it.
 *
 * Its own file rather than a section of `card.ts`: that file sits at 241 lines against this
 * package's 300-line ceiling for a `.ts`, and the delta is nine facets with a parser for most of
 * them. The wire shape is `core/approvals/delta.py` and the rules behind it are
 * `docs/change-delta.md`; four of those rules are rendering rules, and this parser is the only
 * place that can hold them.
 *
 * **Absence is a claim, and it survives the parse.** Every facet is omitted from the payload when
 * the runner had nothing to state, so an absent facet becomes `null` here and never `{}` or `[]`.
 * A fabricated empty facet is a measurement nobody took, and on the screen it is indistinguishable
 * from one that was.
 *
 * **`changed_fields: []` is not `changed_fields` absent.** `[]` is "nothing moved, and NOA has
 * grounds for saying so" — a no-op, or a branch that failed before writing anything. Absent is
 * "NOA cannot say": the write was accepted and the confirming read could not answer, or the
 * evidence carried no before-value, or the value that moved may not be rendered at all. Both would
 * print as "no changes" to a reader that could not tell them apart, and only one of them is a
 * claim.
 *
 * **`verification` stays a `string`**, never narrowed to the four constants below. Same reason
 * `status` in `card.ts` is not narrowed to its own four: a state this build does not know about
 * must reach the screen as itself and be undecidable, rather than fail open into `verified`.
 *
 * **Snake meets camel here and nowhere else.** The receipt's JSONB is an HTTP contract and is
 * snake_case; everything inside this package reads the camelCase view below, so a field renamed
 * upstream breaks in this function rather than in whichever component happened to read it.
 */

/** The four states the runner may publish. Constants because a renderer switches on the value. */
export const VERIFICATION_VERIFIED = 'verified'
export const VERIFICATION_UNAVAILABLE = 'unavailable'
export const VERIFICATION_MISMATCH = 'mismatch'
export const VERIFICATION_NOT_IN_FORCE = 'not_in_force'

/**
 * One field that moved, with the value on each side.
 *
 * `old` and `new` are `unknown` because a target system's value is whatever it is — a boolean, a
 * string, a nested object — and narrowing here would be this app deciding what a field may hold.
 */
export type FieldChange = { field: string; old: unknown; new: unknown }

/**
 * What entered and left a list the change is about.
 *
 * `totalEntries` is `null` when the runner did not read the whole list, which is the ordinary case
 * for one that reads only the lines matching its target.
 */
export type ListDelta = { added: string[]; removed: string[]; totalEntries: number | null }

/**
 * One source the change was driven through, and what it answered afterwards.
 *
 * `driven` is about the commands and `answered` is about the confirming read taken after them.
 * They are two facts about two moments and they fail apart: a backend that ran the commands and
 * then went silent is exactly the case one boolean could not say.
 */
export type BackendOutcome = {
  name: string
  driven: boolean
  answered: boolean
  verdict: string | null
  errorCode: string | null
}

/** The bound of the capped reading a delta's claim rests on. */
export type DeltaBound = { total: number; truncated: boolean }

export type ChangeDelta = {
  /** What the change was about — the machine, and whatever names the thing on it. */
  identity: Record<string, unknown>
  /** One of the four constants above, or a state this build has never heard of. */
  verification: string
  /** A named code for why there is no measurement. `null` on a delta that has one. */
  verificationCause: string | null
  /** `[]` is "nothing moved"; `null` is "NOA cannot say". See the module note. */
  changedFields: FieldChange[] | null
  listDelta: ListDelta | null
  backends: BackendOutcome[] | null
  /** The sources that produced no answer, by name. Never a count — a count says nothing to go and look at. */
  unanswered: string[] | null
  /** Present when a credential NOA generated may be live. Its absence is the claim that it is not. */
  deliveredCredential: string | null
  /** New values with no before twin: a resolved expiry, the window that was asked for. */
  newValues: Record<string, unknown> | null
  bound: DeltaBound | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asNullableString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

/** Every string in a list, dropping anything that is not one. */
function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

/**
 * The rows that moved, or `null` when the key holds something no reader can use.
 *
 * A row nothing can name is dropped rather than rendered as a nameless change; a whole facet that
 * is not a list becomes `null`, because "NOA cannot say" is the safe direction and `[]` would be
 * an explicit "nothing moved" that no runner published.
 *
 * **A non-empty list that yields no readable row is `null`, not `[]`**, and the two inputs are not
 * the same claim. `[]` is the runner saying it compared and nothing moved — a card prints that as
 * "Nothing changed" and the copied summary as a statement about the machine. Rows arriving that
 * this app cannot read is the opposite: something was measured and the measurement is unreadable,
 * which renders as no delta and leaves the receipt's error code to speak. Manufacturing the
 * benign answer out of an unreadable one is the fabrication the whole shape exists to prevent.
 *
 * A *partly* readable list keeps what it can read and drops the rest, which understates the
 * change. Left as it is rather than escalated to `null`: the payload comes from NOA's own writer,
 * so a malformed row is a defect that shows up in the runner and its tests before it reaches here.
 */
function parseFieldChanges(value: unknown): FieldChange[] | null {
  if (!Array.isArray(value)) return null

  const rows = value.flatMap((row) => {
    if (!isRecord(row)) return []
    const field = row['field']
    if (typeof field !== 'string' || field === '') return []
    return [{ field, old: row['old'], new: row['new'] }]
  })

  return rows.length === 0 && value.length > 0 ? null : rows
}

function parseListDelta(value: unknown): ListDelta | null {
  if (!isRecord(value)) return null

  const totalEntries = value['total_entries']
  return {
    added: asStrings(value['added']),
    removed: asStrings(value['removed']),
    totalEntries: typeof totalEntries === 'number' ? totalEntries : null,
  }
}

/**
 * The per-source rows, dropping any the operator could not go and look at.
 *
 * `driven` and `answered` are read fail-closed, as the writer wrote them: a row that cannot say a
 * backend ran the commands has not said so, and a row that cannot say it answered the confirming
 * read has not answered. The opposite direction would turn an unreadable byte into a backend
 * agreeing the change took.
 *
 * The same rule as the field rows above, for the same reason: a non-empty list that yields no
 * readable row is `null`, because an empty backend list states that the change was driven through
 * no source at all, and that is a different answer from "sources were reported and cannot be
 * read". A partly readable list keeps the named rows and drops the rest.
 */
function parseBackends(value: unknown): BackendOutcome[] | null {
  if (!Array.isArray(value)) return null

  const rows = value.flatMap((row) => {
    if (!isRecord(row)) return []
    const name = row['name']
    if (typeof name !== 'string' || name === '') return []
    return [
      {
        name,
        driven: row['driven'] === true,
        answered: row['answered'] === true,
        verdict: asNullableString(row['verdict']),
        errorCode: asNullableString(row['error_code']),
      },
    ]
  })

  return rows.length === 0 && value.length > 0 ? null : rows
}

function parseBound(value: unknown): DeltaBound | null {
  if (!isRecord(value)) return null

  const total = value['total']
  // A bound with no total is not a bound, and rendering it as one would put a number nobody
  // measured beside a claim that rests on it.
  if (typeof total !== 'number') return null

  return { total, truncated: value['truncated'] === true }
}

/**
 * The receipt's `delta` key as a `ChangeDelta`, or `null` if it is not one.
 *
 * `identity` and `verification` are the two fields every delta has, and a payload missing either
 * is not readable as one: a delta that cannot say what it is about describes nothing, and one that
 * cannot say whether it was confirmed is the claim the partial-answer rule refuses. `null` there
 * renders as no delta at all, which is a state the card already has — unlike a card, a delta has
 * a truthful empty rendering, so this can be strict where `parseApprovalCard` cannot.
 */
export function parseChangeDelta(value: unknown): ChangeDelta | null {
  if (!isRecord(value)) return null

  const identity = value['identity']
  const verification = value['verification']
  if (!isRecord(identity) || Object.keys(identity).length === 0) return null
  if (typeof verification !== 'string' || verification === '') return null

  const newValues = value['new_values']
  return {
    identity,
    verification,
    verificationCause: asNullableString(value['verification_cause']),
    // The key's presence is what separates `[]` from absent, and that pair is the whole point of
    // the facet. Nothing that reads the value alone can tell them apart.
    changedFields: 'changed_fields' in value ? parseFieldChanges(value['changed_fields']) : null,
    listDelta: parseListDelta(value['list_delta']),
    backends: parseBackends(value['backends']),
    unanswered: Array.isArray(value['unanswered']) ? asStrings(value['unanswered']) : null,
    deliveredCredential: asNullableString(value['delivered_credential']),
    newValues: isRecord(newValues) ? newValues : null,
    bound: parseBound(value['bound']),
  }
}
