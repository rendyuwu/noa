# Writing a CHANGE runner's before→after delta

Reference for the author of the next CHANGE tool. It exists so the eighth one does not invent an
eighth shape, and so the rules a newcomer would otherwise break are stated rather than inferred
from six examples.

The shape lives in `core/approvals/delta.py`. The seven runners that fill it are under
`apps/api/src/noa_api/mcp_tools/`.

## Why the runner states it, and not a reader

An approved change writes a receipt with two halves. `before` is the gate's in-process preflight,
persisted on `action_requests.approval_context` at the moment the operator was asked. `after` is
what the runner answered, redacted. Both are stored whole and neither is going away.

Where their keys meet, they meet on identity. `before` for a WHM account change carries
`{"account": {"user": "acmeco", "suspended": false}, ...}`; `after` carries
`{"ok": true, "server": "alpha", "username": "acmeco", "suspended": true, ...}`. The overlap
across the seven CHANGE tools runs from one key to five — `server` on the account pair, and
`server`, `node`, `vmid`, `net`, `action` on `proxmox_vm_nic` — and every one of those keys holds
the *same value* in both halves, because a runner resolves the machine and the thing on it out of
the evidence rather than re-deriving them. The field that moved is never among them:
`before["nic"]["link_state"]` against `after["link_state"]`,
`before["account"]["suspended"]` against `after["suspended"]`.

So there is no key alignment for a reader to perform — the shared keys cannot yield a change and
the change is not under a shared key — and the only party that holds both vocabularies is the
runner: it read one and produced the other. That property is held by
`apps/api/tests/test_change_receipt_halves.py`, which drives each tool's gate and then its runner
and compares the two halves a receipt is built from. It was prose alone until then, and the prose
had the count wrong — a fixture asserting a property of itself, one surface over.

It also knows things neither half records. Which backend answered the confirming read. Whether
the write landed and the step that puts it into effect did not. Whether a credential was
delivered. None of that has a key in `after` to survive in.

## Why it does not ride in the payload

The tempting mechanism is to put the delta in the runner's return dict and let `build_receipt`
lift it out the way it lifts `ok` and `error_code`. That contradicts three of its own goals at
once, and quietly damages a fourth thing:

- `ok` and `error_code` are read from the **raw** payload, before redaction. "Lifted like those"
  means lifted unredacted.
- `after` **is** the redacted payload. A delta in the payload puts a key in `after`, and the
  promise that `after` is byte-for-byte what it was becomes unstateable.
- `tool_runs.result_summary` is that same payload as compact JSON, cut at 2000 characters with
  the tail replaced by an ellipsis (`core/audit/summaries.py`). A delta in the payload pushes
  existing content toward that cut. Nothing errors. An audit field simply loses its tail — in
  front of the admin surface, the card's execution-result line and `noa_get_action_result` — and
  a byte-identity test on `before` and `after` would never see it. Measured on
  `whm_firewall_release_and_allow`, the payload the fold costs the most: 391 characters today,
  822 with its runner-test delta folded in and 860 with the delta a live gate's evidence
  produces. (The largest payload on its own is `proxmox_reset_vm_password` at 424 — a different
  superlative, and the one that does not matter here. Both are measured in
  `apps/api/tests/test_change_receipt_halves.py`.)
- The receipt dict gains exactly one key, as one named edit, instead of as a side effect.

So a runner returns a `ChangeOutcome`: the envelope, and the delta beside it.
`build_receipt` takes the delta as its own parameter and redacts it on its own line.

```python
return ChangeOutcome(
    payload=tool_ok(**common, status=STATUS_CHANGED, verified=True, message=...),
    delta=ChangeDelta(
        identity={"server": target.server_name, "username": target.username},
        verification=VERIFICATION_VERIFIED,
        changed_fields=(FieldChange(field="suspended", old=False, new=True),),
    ),
)
```

Returning the bare envelope is still a complete answer and means "nothing measured worth
stating". `ChangeRunner`'s return type is the union of the two, and the executor normalises.

## The shape

`identity` and `verification` are always present. Everything else is a facet, `Optional` in the
type and omitted from `as_payload()` when it is `None`.

| facet | what it is for | who fills it |
|---|---|---|
| `identity` | what the change was about, per family | all seven |
| `verification` | one field, four states | all seven |
| `verification_cause` | a named code for why there is no measurement | any state but `verified` |
| `changed_fields` | one row per field that moved, `{field, old, new}` | the account pair, `proxmox_vm_nic`, `whm_firewall_release_and_allow` |
| `list_delta` | what entered and left a list | `pmg_whitelist`, `whm_firewall_allowlist_remove` |
| `backends` | per-source `{name, driven, answered, verdict?, error_code?}` | the two firewall tools |
| `unanswered` | the sources that produced no answer, by name | the two firewall tools |
| `delivered_credential` | the "nothing pairs" slot | `proxmox_reset_vm_password` |
| `new_values` | new values with no before twin | `whm_firewall_release_and_allow` |
| `bound` | `{total, truncated}` for a capped list the claim rests on | the two firewall tools |

It is deliberately **not** a discriminated union. A union means one value selects exactly one
member, and `whm_firewall_release_and_allow` fills four facets in a single answer — the
per-backend map, the unanswered list, the resolved expiry and the verdict transition. A union
would have needed a synthetic tag for that combination on the day it was written, and another for
the next tool that combined two differently.

## The rules a newcomer breaks

### Absence renders as absence, not as "no"

Every facet is optional in the type, so a missing outcome is *absent* rather than `false`. A
non-optional field would force a value where nothing was measured, and a fabricated `false` reads
exactly like a measured one — which is the failure the partial-answer rule exists to stop, applied one surface
over. `apps/api/tests/test_change_delta.py` asserts the facet record has exactly two required
fields, so a facet added without a default fails there rather than in a card six months later.

### `changed_fields = ()` and `changed_fields = None` are different claims

This is the distinction most likely to be got wrong, and it is invisible in a rendered card.

- `()` — nothing moved, and NOA has grounds for saying so. A no-op. A write the target system
  refused and said so. A branch that failed *before* anything was written.
- `None` — NOA cannot say. The write was accepted and the confirming read could not answer. The
  evidence carried no before-value to compare against. Or the value that changed is one that may
  not be rendered at all.

Both would print as "no changes" to a reader who could not tell them apart, and only one of them
is a claim. Pick by asking: *can NOA state that nothing moved, and on what?* Grounds is narrow —
a reading was taken, or the write is known not to have been issued. A write that was accepted and
then lost, to a timeout or a dropped connection or a task nobody read back, is `None`: the change
may have landed.

The `old` side is the second half of the same question, and it fails the same way. Where the
evidence carries no before-value the answer is `None`, never `()`: an empty diff says NOA
compared the reading against the card and found it unmoved, and on the confirmed branch that is
said about a change the postflight has just watched happen. Three runners take that decision —
`whm_account_change_runner.py`, `proxmox_nic_runner.py` and `whm_firewall_change.py` — and all
three answer `None`.

The worked case is `proxmox_vm_nic` (`proxmox_nic_runner.py`). Its `no_op` branch answers
`verified: true` having written nothing, so a naive before→after diff of its payload renders the
identity fields as new values and describes a change to an interface nothing touched. That branch
publishes `changed_fields=()` — present, empty, explicit. Its `task_timeout` branch publishes
`None`: Proxmox accepted the write and its outcome was never read, so the link may already have
moved.

### Verification has four states, not two

Genuinely four, and each is already a distinct branch in the code:

1. `verified` — the postflight answered and agrees.
2. `unavailable` — NOA holds no measurement. `verification_cause` names why: the confirming read
   did not answer, or the change was refused before one could be taken. **Not verified, and not
   refuted either.**
3. `mismatch` — the postflight answered and *disagrees*. That is a measurement, and it is a
   failure. `proxmox_nic_runner.py` and `pmg_whitelist_runner.py` both carry a `postflight_failed`
   code whose comment already says so: "Distinct from the unavailable case: this is a
   measurement."
4. `not_in_force` — the write landed and the step that applies it did not.
   `pmg_whitelist_runner.py`'s `pmg_sync_failed`: "The config moved and Postfix did not, which is
   neither a change nor a refusal." Neither `verified` nor a bare failure says that — the first is
   a lie and the second sends an operator back to re-add an entry that is already in the config.

A `verification_cause` on a `verified` delta is refused at construction: a reason for a non-answer
cannot ride on an answer.

### A bounded list ships its bound

The cap's own bound. `before.firewall.matches` is cut at twenty by csf's own `max_matches`, and travels with
`total_matches` and `truncated`. A delta stated against that reading carries the same pair, or
"this address was blocked and is now allowed" reads as a statement about every line the firewall
holds for the address. `whm_firewall_change_common.py::evidence_bound` lifts it off the evidence
and answers `None` — not `0` — when the evidence has no usable pair: a bound nobody recorded is
not a bound of nothing.

### Name the source that could not answer

The partial-answer rule. Names, never a count: "one backend was silent" does not say which server to go and look
at. `whm_firewall_change_common.py::unanswered_backends` is the one spelling of the predicate, and
it is asked on the before-state and on every after-state branch. On the branch where a backend
went silent, no `verification_cause` rides beside the names — *which* source said nothing is the
cause, and a code next to it would be a second answer to one question.

### Per-backend rows render regardless of the envelope's `ok`

`driven` and `answered` are two facts about two different moments, joined by name in
`backend_outcomes`. A backend that ran the commands and then went silent on the confirming read is
the case the pair exists for, and one boolean could not say it. Both render on a failure: a
refused change still measured which backend refused it and which ones answered.

### A no-op has no receipt to render at all

The three tools with a no-op branch — `whm_suspend_account`, `whm_unsuspend_account`,
`whm_firewall_allowlist_remove` — answer it at **tool** time, before any approval request exists.
No card, no run, no receipt. So a delta is never rendered for those, and the no-op deltas that do
exist belong to the runners that discover the no-op only *after* an approval:
`proxmox_vm_nic` and `pmg_whitelist` re-read and find the world already as the operator wanted it.
Those publish an explicit "nothing changed" in their family's own facet — an empty
`changed_fields` and an empty `list_delta` respectively.

### The delta carries no reason

The reason boundary. One reason field exists, an operator types it on the card, and the LLM
never authors, relays or sees it. `change_runners.py`'s own docstring states the payload half: a
runner must not echo the reason back in its payload, because `tool_runs.result_summary` is
derived from that payload and `noa_get_action_result` hands the summary to a model.

**The receipt is not that door**, and it is worth being exact about which one is: the model-facing
reader never loads a receipt row (`core/approvals/results.py`). What it does take is a
projection of exactly two of the delta's fields, `verification` and `verification_cause`, lifted
out of the JSONB **in SQL** and answered as `change_verification` and `change_verification_cause`
— because a mutation that timed out leaves NOA holding no reading, and a model told only that
the run failed reports to the operator that nothing happened. Both are NOA's own vocabulary: a
state from a fixed set, and a named cause. Neither receipt half is fetched, so `before` — which
on a WHM account carries `suspendreason` — does not enter that process at all. Widening the pair
means adding a column to that statement, which is a deliberate act and not an omission. What
else reaches a model is `result_summary`, and nothing beyond these.

A delta is otherwise a *receipt* key, read by the approval card and the
admin audit surface — both the operator's own, behind their cookie — and the fence exists there
anyway, because those two surfaces are where the operator's words would be reflected back at
them, and because a future reader that does pass the flag must not be the moment the question is
first asked. So `ChangeDelta` refuses a reason-bearing key anywhere inside it, at construction,
with a recursive walk — the shape `core/secrets/redaction.py` uses for credentials.

`suspendreason` is on the refused list because WHM stores the operator's note and echoes it back
on every later `listaccts` row, including the postflight read `whm_account_change_runner.py`
takes. The words can therefore arrive at a runner from the *target system* and not only from the
request it was handed. The way to stay clear of the refusal is the way that runner does it: build
the identity from named strings, never from a summary a target system gave you.

## Choosing a facet for a new tool

Ask what kind of thing moved.

- **A field's value changed** → `changed_fields`. `whm_account_change_runner.py` moves
  `suspended`; `proxmox_nic_runner.py` moves `link_state`. The `old` side comes off the
  **evidence**, never off a second reading the runner takes: the evidence is what the operator
  authorised against, and a re-derived `old` is a delta about a decision nobody made. Where the
  evidence cannot say, state no field change at all — an `old` side nobody recorded is not an
  `old` side of `false`.
- **Something entered or left a list** → `list_delta`, holding the target system's own spelling of
  each line. `pmg_whitelist_runner.py` names `entry.cidr` because `mynetworks` holds `1.2.3.4` and
  `1.2.3.4/32` as two lines and one entry, and a receipt saying "removed" without saying which
  lines cannot be checked against the box. `total_entries` comes off the evidence, because that is
  the only place the whole list was counted.
- **The change is list membership even though a verdict exists** → still `list_delta`, and the
  block-first conflict rule is why. `whm_firewall_allowlist_remove` could have used the combined verdict, but both
  backends resolve a conflict block-first: an address on a deny list *and* an allow list reads
  `blocked` before the removal and `blocked` after it, so a verdict-based delta would render
  "nothing changed" for a removal that worked, on exactly the address that most plainly had an
  entry to delete.
- **The new value must not be rendered** → `delivered_credential`, and no `changed_fields` on any
  branch. `proxmox_password_runner.py` changes a password: the old one NOA never held, the new one
  it must not record, and a crypt hash of either stays in the runner's frame rather than reaching
  a payload a model can read. The facet's *presence* is the claim that the credential may be live
  — the same rule the payload's `yopass_url` follows, read off the same field so the two cannot
  disagree.
- **A new value with no before twin** → `new_values`. `whm_firewall_release_and_allow` resolves a
  window into an absolute expiry; there was no expiry before. It rides where the command that
  wrote it was accepted **and** the postflight did not refute the entry, and it is absent on both
  of the other branches — where a backend could not be driven, and where every backend answered
  `not_found`, which is each of them saying it holds nothing for this address. A resolved
  timestamp for an entry no command created is the worst kind of precise, and a facet keyed off
  the commands alone publishes exactly that on the branch whose own verdict says the entry is not
  there. A `blocked` verdict is not that refutation: both backends resolve a conflict block-first,
  so an allow written under a surviving deny entry exists and is overridden, and whether it takes
  effect is the verdict's job.

If none of these fits the new tool, that is worth a conversation before a facet is added: the cost
of an eighth facet is that every renderer grows a branch, and the cost of reusing one badly is a
receipt that reads as a measurement it is not.

## What the facet looks like pasted into a ticket

A delta has two readers. The approval card renders it for an operator deciding something now, and
the copy-summary block carries it out of the frame for whoever reads the ticket six months later.
The builder is `apps/web-embed/src/lib/approvals/summary.ts`.

**What the blocks below are, exactly.** They are that builder's own output, run over two fixtures
whose keys are copied from the runners named beside them — not hand-composed, and not a capture of
a live run either. So the key names and the shape are the real ones and the values are invented.
The binding record of what the builder does with any given input is its own suite,
`apps/web-embed/src/lib/approvals/summary.test.ts`; these are here to show a reader what lands in a
ticket, and if the builder changes they will drift until someone re-runs it.

The copied block deliberately states **more** than the card renders. No image reaches the clipboard
from inside the frame on the pinned host, so this text is the only durable record that can leave —
and a field the card drops as noise on screen is exactly the field a ticket is searched by later.
Hence the reference block at the end: the request id, the run id, the LibreChat account and the
conversation reference, none of which the card displays any more. Every timestamp is absolute and
carries `+07:00` (`apps/web-embed/src/lib/format/jakarta-time.ts`), because a bare wall-clock time
is read in whatever zone the reader is in.

### A firewall change with a multi-backend result

One backend answered the confirming read and one did not, on the branch where the commands were
accepted. `whm_firewall_change.py::_release_delta` states why each facet falls the way it does
here: a silent backend leaves `measured_verdict` at `None`, a comparison needs both sides, so
`changed_fields` is **absent** rather than empty; the resolved expiry still rides as a new value
because every command was accepted and no backend answered that it holds nothing; and the reading
the verdict rests on was capped at twenty lines by csf's own `max_matches`.

```text
NOA approval record

Change
  Tool: whm_firewall_release_and_allow
  Status: Approved
  Target: server=alpha, target=203.0.113.24

What moved
  Verification: unavailable
  Field changes: not measured. Nothing here says that nothing moved.
  New values: expires_at=2026-09-11T11:06:12+07:00, duration_minutes=60

Result
  The change completed.
  Backend alpha: driven, answered, verdict allowlisted
  Backend beta: driven, no answer
  Unanswered sources: beta
  Evidence bound: 20 entries, truncated — the claim above rests on a capped reading, not on the whole list.

Timing
  Requested: 2026-09-11 09:00:00 +07:00
  Approval window ends: 2026-09-11 10:00:00 +07:00
  Decided: 2026-09-11 09:06:12 +07:00
  Run status: COMPLETED
  Run started: 2026-09-11 09:06:12 +07:00
  Run completed: 2026-09-11 09:06:20 +07:00 (8.0s)

Reference
  Request id: 7d3a1c02-9e44-4f61-b0d2-5c8ea1f37b90
  Run id: 5c2f1a90-0000-4000-8000-000000000001
  LibreChat account: operator@noa.internal (user librechat-user-1)
  Conversation: 1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12
```

Three things in that block are the rules above, seen from the reading end.

`The change completed.` and `Verification: unavailable` sit four lines apart and do not contradict
each other: the first is the runner's envelope and the second is whether anything read the target
back. A block that let the envelope speak for both would be the collapse this whole document exists
to prevent.

`Unanswered sources: beta` names the machine. A count would tell an operator that something is
wrong and not where to go, and no `verification_cause` rides beside it because *which* source said
nothing is the cause.

`New values` prints the runner's own pair verbatim — an ISO timestamp and the window it came from,
because `renderValue` returns a string as it stands and does not reformat one. The absolute
timestamps in the `Timing` block below are a different thing: those are the card's own fields, put
through the Jakarta formatter.

`Field changes: not measured` is printed rather than omitted. That facet and the unanswered-sources
line are the two that print in all three of their states — measured, measured-as-nothing, and not
measured — because for those two the benign reading of a hole is the one that misleads. Every other
absent facet is simply left out: a missing `list_delta` says this family's change is not list
membership, which is nothing to report.

### A mail gateway list change

An entry was added to `mynetworks` and the step that puts it into effect did not run — the state
that is neither a change nor a refusal.

```text
NOA approval record

Change
  Tool: pmg_whitelist
  Status: Approved
  Target: server=mail-1, action=add, target=198.51.100.7, normalized_target=198.51.100.7/32

What moved
  Verification: not_in_force (pmg_sync_failed)
  Field changes: not measured. Nothing here says that nothing moved.
  List entries added: 198.51.100.7/32
  List entries removed: none
  List size: 42 entries

Result
  The change did not complete.
  Error code: pmg_sync_failed
  Unanswered sources: not measured.

Timing
  Requested: 2026-09-11 11:10:00 +07:00
  Approval window ends: 2026-09-11 12:10:00 +07:00
  Decided: 2026-09-11 11:12:30 +07:00
  Run status: FAILED
  Run started: 2026-09-11 11:12:30 +07:00
  Run completed: 2026-09-11 11:14:06 +07:00 (1m 36s)

Reference
  Request id: b41f8e77-2c05-4a9d-8f13-6ad0c9e25a44
  Run id: 5c2f1a90-0000-4000-8000-000000000002
  LibreChat account: operator@noa.internal (user librechat-user-1)
  Conversation: 9c7d2b10-4e88-4a51-b7c6-2e4f1a09d833
```

`List entries added` carries the gateway's own spelling of the line, `198.51.100.7/32`, beside the
`198.51.100.7` the operator typed — both are in the identity line for the same reason. An operator
holding this block can go to the box and grep for either.

The line an author of a new runner should look at hardest is `Verification: not_in_force
(pmg_sync_failed)` sitting above `The change did not complete.` Read together they say the config
moved and Postfix did not, which is the one state that sends an operator to the right place. A
runner that answered a bare failure here would send them to re-add an entry that is already in the
file.

Two things a delta must never put in this block. **A reason**: the operator's justification is
typed at decision time and travels outward only, and the builder has nowhere to put one because the
card carries none back. **A credential's delivery link**: a delivered credential is *stated* in the
copied block and its one-open URL is not, because a link pasted into a ticket is spent by whoever
reads the ticket first. The link stays on the card.
