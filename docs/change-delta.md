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

**`None` covers two facts, and the sentence on the card tells them apart even though this field
cannot.** A delta says `None` both where the evidence carried no reading at all and where a
perfectly good gate-time reading exists but the postflight could not confirm a change against it.
For the delta those collapse correctly — neither one can state a field change. For a person they
do not: the first means NOA knows nothing about the prior state, and the second means NOA knows
exactly what the operator approved against and simply could not re-check it. So the runners that
compose an operator-facing before-clause spell those two differently, and the grammar carries it:
*"It was `<old>` when NOA last read it"* claims only a reading, while *"It was `<old>` before this
ran"* claims a comparison. Only a genuinely absent `old` earns *"NOA has no reading of what it was
before."*

The trap when writing a new runner is testing that `old` side for truthiness. `suspended: false`
and an absent `suspended` are two different facts, and `if not old:` sends the first down the
second's path — which is how an unconfirmed card came to tell an operator NOA held no reading
while the reading sat in `approval_context`. Test `is None`.

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

### A write that failed is not the end of the story

Every CHANGE runner re-reads its target after it writes, and it does so whether or not the write
succeeded. When the write failed, the reading decides the verdict — but only in one direction.

A state that matches the change after a call that never answered is `verified`, with no cause and
no qualifier: NOA sent the write, the remote took the connection, and the state is what was asked
for. A state that does *not* match after the same call is `unavailable` with the write's own code,
never a claim that the change did not happen, because a change landing a second after NOA stopped
waiting reads exactly like one that never landed.

When the remote **refused** — it answered, and said it did nothing — the reading becomes
conclusive. A disagreeing read is `mismatch` with an empty diff. An agreeing one is `unavailable`
with the refusal's code, because something other than this change put the state there.

A confirming read that itself fails reports `unavailable` with the *read's* cause, not the write's;
the write's code stays on the envelope, where a reader looks for what failed.

Two tools depart from this, both deliberately. The firewall pair answers per backend, so a positive
reading cannot make a change whole when a named backend refused its command — `verified` is
unreachable on that branch, and `unanswered` still names every source that could not answer.
`pmg_whitelist` keeps its own answer where the write landed and `pmgconfig sync` did not: that is
`not_in_force`, a measurement a fresh read cannot add to, since `mynetworks` will read back exactly
as written.

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

## What a change looks like pasted into a ticket

A delta has two readers, and **neither of them prints a facet any more**. The approval card renders
a change for an operator deciding something now, and the copy-summary block carries the same body
out of the frame for whoever reads the ticket six months later — and what each of them shows is the
runner's own sentence, the gate's own evidence, and one word in the corner. Of the delta itself the
two surfaces read, between them, exactly two fields: `verification`, which becomes that corner word
on both, and `delivered_credential`, whose link renders on the card and never in the block. Every
other facet is still stated by the runner, still on the receipt, and still rendered whole on
`/admin` — the audit drawer draws the delta as JSON under `Change`, both receipt halves beside it,
and the envelope's error code as its own row
(`apps/admin-web/src/components/admin/audit/action-request-detail-drawer.tsx`). What changed is the
surface that prints them, not what a runner states.

The builder is `apps/web-embed/src/lib/approvals/summary.ts`, with what each section says in
`summary-sections.ts` beside it. Both read `lib/approvals/body.ts`, which is what the card renders
too, so the screen and the ticket cannot become two statements of one measurement; the evidence
block inside that body is `lib/approvals/evidence.ts` and the corner is `lib/approvals/verdict.ts`.

**What the blocks below are, exactly.** They are that builder's own output, run over two fixtures
whose keys are copied from the runners named beside them — not hand-composed, and not a capture of
a live run either. So the key names and the shape are the real ones and the values are invented.
The binding record of what the builder does with any given input is its own two suites,
`apps/web-embed/src/lib/approvals/summary.test.ts` and `summary-evidence.test.ts` beside it, and
that the card says the same thing byte for byte is
`apps/web-embed/src/app/approvals/[id]/card-parity.test.tsx`; these are here to show a reader what
lands in a ticket, and if the builder changes they will drift until someone re-runs it.

The copied block carries what the card cannot. No image reaches the clipboard from inside the frame
on the pinned host, so this text is the only durable record that can leave — and a field the card
drops as noise on screen may still be the field a ticket is answered from later. Hence the `For
support` block at the end, and hence its **one** identifier: the run id, beside the requester's
email and nothing else. There is no tool-name row in it at all — the raw tool name survives on one
embed surface only, as the hover `title` of the card's heading, which is a weak affordance and
therefore never the only place a fact lives. The run id is on both: the card's `Requested` block
and the end of this one. The action-request id, the conversation reference and the LibreChat
account all render in the admin drawer for whoever can open `/admin`, and the audit list and that
drawer are keyed on the run id — so the run id is the one string that gets an answer for a reader
who cannot (`DECISIONS.md` section 15: the copied block carries exactly one identifier an
administrator can look the run up by, `tool_runs.id`, beside the requester's email).

Every timestamp this builder renders is absolute and **bare**. The `When` heading names Jakarta
(WIB) once and the stamps under it carry no offset
(`apps/web-embed/src/lib/format/jakarta-time.ts`): a wall-clock time is read in whatever zone the
reader sits in, so the zone is stated — once, above the values it governs, rather than on every line
until it reads as furniture. A stamp a *runner* composed into its own sentence is the other case and
it carries `(WIB)` on the value, because that sentence also renders on the card, which has no
zone-naming heading to put a bare stamp under.

### A firewall change with a multi-backend result

One backend answered the confirming read and one did not, on the branch where the commands were
accepted — answer 2 of the five in `whm_firewall_release_outcome.py::release_outcome`, which is
where this tool's outcome and its delta moved when `whm_firewall_change.py` ran out of room under
the 900-line cap. The delta that branch publishes is what it always was, and
`whm_firewall_release_outcome.py::release_delta` still states why each facet falls the way it does:
a silent backend leaves `measured_verdict` at `None`, a comparison needs both sides, so
`changed_fields` is **absent** rather than empty; the resolved expiry still rides as a new value
because every command was accepted and no backend answered that it holds nothing; and `bound`
carries the cap the gate's reading was taken under. None of the three prints as a row any more.
What an operator gets instead is this.

```text
NOA approval record

IP unblocked — 203.0.113.24 — Approved · not confirmed
  The unblock ran on alpha. Imunify did not answer when NOA checked afterwards, so NOA cannot say it is fully unblocked.

Why it was blocked
  Found 203.0.113.24 in /etc/csf/csf.deny
  Temporary Blocks: IP:203.0.113.24 Port: Dir:in TTL:1800
  Imunify blacklist: 203.0.113.24 (brute force)
  Read from the server before the change ran.

When — all times Jakarta (WIB)
  Requested: 2026-09-11 09:00:00
  Decided:   2026-09-11 09:06:12
  Finished:  2026-09-11 09:06:20

For support
  Requested by: operator@noa.internal
  Run id:       5c2f1a90-0000-4000-8000-000000000001
```

Five things in that block are the rules above, seen from the reading end.

**The headline states two facts, and here they disagree on purpose.** `Approved` is the decision an
operator made; `not confirmed` after the middle dot is what the change then did, read off the
delta's `verification` — `unavailable` on this branch — rather than off the call's return. That
corner word is where the four verification states landed, and all four are still told apart in it:
`verified` adds nothing at all, `unavailable` is `not confirmed`, `mismatch` is `did not happen`,
`not_in_force` is `not live yet`, and a state this build has never heard of is quoted back as
`NOA reported "<state>"` rather than folded into any of them (`lib/approvals/verdict.ts`). Note what
the headline itself does *not* do: `IP unblocked — 203.0.113.24` is the runner's own and it reads
identically on the branch where the postflight agreed, because WHM accepted the call in both cases.
The corner is the only thing separating a confirmed change from an unread one, which is why it is
never allowed to be one word.

**The silent backend is named in the runner's sentence, and that is where `Unanswered sources`
went.** `Imunify did not answer when NOA checked afterwards` is composed in Python by
`whm_firewall_change_common.py::name_sources`, which joins the backends' display names — named,
never counted, because "one backend was silent" tells an operator that something is wrong and not
which box to go and look at. No `verification_cause` rides beside it, in the sentence or on the
delta: *which* source said nothing is the cause, and a code next to it would be a second answer to
one question. The names are still on `delta.unanswered` for `/admin` to draw.

**`Field changes: not measured` no longer prints, and the fact it carried is now in two places at
once.** The corner's `not confirmed` says NOA holds no measurement, and the runner's sentence says
which reading is missing and what it would have settled. What genuinely left the block is the diff
rows themselves, and one distinction with them: a verified change that moved a field and a verified
change that measured nothing moving both paste as `Approved` with the runner's sentence under them,
and only the receipt tells those two apart. The families that compose an operator-facing
before-clause carry that split in grammar instead, which is the `None`-versus-`()` rule above said
in a sentence rather than in a field nobody can see — *"It was `<old>` before this ran"* claims a
comparison, *"It was `<old>` when NOA last read it"* claims only a reading, and *"NOA has no reading
of what it was before."* is the genuinely absent `old` (`whm_account_change_runner.py`,
`proxmox_nic_runner.py`).

**`New values` is gone, taking the last `+07:00` with it, and this branch states no expiry at all.**
The resolved expiry still rides on the delta, and where a branch does state one to an operator it is
the runner's own stamp: `whm_firewall_release_outcome.py::jakarta_stamp` writes
`11 Sep 2026, 11:06 AM (WIB)` into the sentence, with the zone on the value because the card has no
zone-naming heading to put a bare stamp under. This branch deliberately states none — a card whose
corner says NOA could not confirm the change must not print a precise time for a window it cannot
confirm is in force (owner-decided, stated at the branch). So an ISO string carrying its own offset
no longer reaches either surface: every stamp a reader sees is either bare under the `When` heading,
which names Jakarta once for all of them, or carries `(WIB)` on its own value.

**The evidence block is new here, and it is the reading the decision rested on.** `Why it was
blocked` is the gate's own heading, written in Python beside the family whose vocabulary it is
(`change_gate.py`, `EVIDENCE_HEADING`); the lines are CSF's grep output and Imunify's rows exactly
as each printed them, so an operator can check either against the box; and the closing line is where
`Evidence bound` went. **A capped reading states its cap and an uncapped one says nothing at all** —
a sentence claiming completeness on every uncapped block would read as furniture within two cards.
The reading above was not cut, so the line is `Read from the server before the change ran.`; one cut
by csf's own `max_matches` of 20 ends `…before the change ran; this is the first 20 of 34 lines.`
instead, and a `truncated` flag with no readable total still says it was cut without inventing a
number (`lib/approvals/evidence.ts`, `closingLine`). The separating case that would otherwise let an
unconditionally appended sentence pass is in `summary-evidence.test.ts`.

### A mail gateway list change

An entry was added to `mynetworks` and the step that puts it into effect did not run — the state
that is neither a change nor a refusal.

```text
NOA approval record

Saved, not live — 198.51.100.7 — Approved · not live yet
  198.51.100.7 was added to the list on mail-1 as 198.51.100.7/32, and the step that puts it into effect did not run. It cannot relay email yet.

When — all times Jakarta (WIB)
  Requested: 2026-09-11 11:10:00
  Decided:   2026-09-11 11:12:30
  Finished:  2026-09-11 11:14:06

For support
  Requested by: operator@noa.internal
  Run id:       5c2f1a90-0000-4000-8000-000000000002
```

**This block has no evidence section, and that absence is the gate's decision rather than a hole.**
A PMG *add* publishes no `evidence_heading`: its matching entries are empty because the address is
not on the list, which is why it is being added, and that emptiness *is* the before-state. The key's
presence is the decision — heading present, the block is drawn; heading absent, nothing is drawn at
all, never an empty heading over nothing (`lib/approvals/evidence.ts`;
`summary-sections.ts::evidenceSection` answers `null` and `summary.ts` then emits no section, which
is the same decision the card makes from the same key). A PMG *remove* does publish one, `What was
on the list`, over exactly the lines the change is about to delete.

**Both spellings of the address are in the runner's sentence, which is where the identity line and
the `List entries` rows went.** `198.51.100.7 was added to the list on mail-1 as 198.51.100.7/32`
carries what the operator typed beside what `mynetworks` actually holds, so an operator holding this
block can go to the box and grep for either — `mynetworks` keeps `1.2.3.4` and `1.2.3.4/32` as two
lines and one entry, and a receipt that named only one of them cannot be checked against the file.
On a removal that same sentence names every line the write actually took out, read off the write
rather than recomputed from the target. The lines are still on `list_delta` for `/admin`, and the
whole-list count that printed as `List size: 42 entries` is now on the receipt only: it comes off
the evidence, which is the one place the whole list was counted.

The line an author of a new runner should look at hardest is `…and the step that puts it into effect
did not run. It cannot relay email yet.`, read against `not live yet` in the corner beside
`Approved`. Together they say the config moved and Postfix did not, which is the one state that
sends an operator to the right place. A runner that answered a bare failure here would send them to
re-add an entry that is already in the file, and one that answered a bare success would leave a
gateway that silently relays nothing.

The code itself, `pmg_sync_failed`, prints on neither embed surface now. It is on the receipt in
both halves of its own — the envelope's `error_code`, which the admin drawer draws as its own row,
and the delta's `verification_cause` — and it is one half of the two-field projection the
model-facing reader is handed, `change_verification_cause` beside `change_verification`, lifted out
of the JSONB in SQL and described in full under the reason boundary above. `pmgconfig`'s own
failure line stays off the sentence and rides on the payload's `sync_error`, because it is an
engineer's text that can quote a command an operator cannot run, while the remedy the sentence gives
does not move with it.

Two things a delta must never put in this block. **A reason**: the operator's justification is
typed at decision time and travels outward only, and the builder has nowhere to put one because the
card carries none back. **A credential's delivery link**: a delivered credential is *stated* in the
copied block and its URL is not, because whoever reads the ticket it was pasted into can use the
link. The link stays on the card.

That rule has not changed and the reason given for it used to be false. It read "a link pasted into
a ticket is spent by whoever reads the ticket first", which describes a one-time link — and whether
a link is one-time is a deployment setting (`YOPASS_ONE_TIME`), off in this one. What actually
happens here is worse rather than milder: the link stays fetchable until it expires, so a ticket
carrying it hands the password to everyone who reads that ticket for as long as it lives. Neither
the copied block nor this document states which setting is in force, because both would then be
true for one deployment and silently false for the next; the runner's own sentence says how long
the link keeps working, composed where the settings are legible, and
`docs/integrations/yopass.md` holds the variables themselves.
