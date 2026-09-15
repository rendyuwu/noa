"""The password-reset caveat quotes the operator's sentence rather than paraphrasing it.

`DECISIONS.md` section 18 makes an owner-stated clause on an approval surface citable by recording
it under `docs/integrations/`, and the Proxmox caveat block says the reset tool's result message
says "exactly that, in those words". Nothing enforced that claim. It had already drifted: before
the stop-and-start rewording, the doc's third clause read "so the restart is done from the customer
portal or from Proxmox" while the card said "restart it from the customer portal or from Proxmox".
A bad import fails `tsc` and a bad SQL column fails at runtime; a doc sentence that stops matching
the code it cites fails nowhere, which is why this is a test and not a review habit.

**Direction matters.** The clauses are derived from the constant and looked for in the document,
never the reverse. A hand-kept list of expected sentences would be a claim about the card rather
than a reading of it, and would go stale the next time the owner supplies new bytes.

**What this does not check, stated because the obvious reading is wider than the mechanism.** The
predicate is that the caveat block carries every clause of the constant, in the constant's order.
It is not adjacency and it is not meaning. Three things still pass, and all three were measured
passing: the constant and the document reworded the same wrong way together; the clauses spread
across separate bullets so long as their order holds; and a bullet that quotes a clause in order to
deny it ("and it is not true that ..."). What the order requirement buys is the drift that actually
happened here — a clause restated in the document's own voice, or deleted from the bullet that
quotes the sentence and left lying somewhere else in the section.

**Constant-side drift is only half-caught here, and the other half lives elsewhere.** A reworded
constant reddens this file when its new wording is absent from the document, which is the common
case, and it also reddens when the new wording sits in the document out of order. What survives is
narrow and was measured: replace the permission-boundary clause with `no tool here starts, stops or
reboots one` — wording the document already carries in exactly that position, between the clauses
either side of it — and this file is green at 4 passed while the card has lost the clause
`proxmox_password_runner.py` calls the reason the sentence exists. What catches that one is the
hand-spelled copy of the whole message in `test_proxmox_reset_password_runner.py` (8 failed, 27
passed), kept uncompiled from the constant on purpose. Anyone tempted to DRY that copy into an
import would take this construction out of the repo's reach entirely; the comment above it already
argues against the import for the neighbouring reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from noa_api.mcp_tools.proxmox_password_runner import MESSAGE_STOP_START_REQUIRED

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = REPO_ROOT / "docs" / "integrations" / "proxmox.md"

# The heading the caveats live under. Part of the contract: a rename that orphans the extraction
# has to fail here rather than silently stop checking, which is why `_caveat_block` raises.
CAVEAT_HEADING = "## Caveats for the tools that will use this"

# The card sentence is one line of prose; the document wraps it across several and splits it with
# other words. Both sides are flattened before they are compared, so the comparison is about the
# words rather than about where a line happens to break.
#
# Whitespace and case only — not markup. Bold *around* a whole clause is invisible here because it
# falls outside the words being compared, but emphasis *inside* one is not: writing a clause as
# `**... the VM is *stopped and started* again**` reports that clause missing even though no word
# changed. Read that failure as "the markup moved", not as drift.
_WHITESPACE = re.compile(r"\s+")

# The constant's own punctuation is what divides it: a full stop between its two sentences, an em
# dash before the instruction. Derived rather than listed, so a reworded sentence with a different
# number of clauses is still fully checked.
_CLAUSE_SPLIT = re.compile(r"\.\s|\s—\s")


def _flat(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().lower()


def _caveat_block(document: str) -> str:
    """The caveats section, or a failure. Never an empty string.

    An extraction that returned `""` on a renamed heading would make every assertion below pass
    against nothing, which is the quiet way a doc test stops being one.
    """
    if CAVEAT_HEADING not in document:
        raise AssertionError(f"{DOC} no longer has the heading {CAVEAT_HEADING!r}")
    after = document.split(CAVEAT_HEADING, 1)[1]
    return after.split("\n## ", 1)[0]


def _clauses(sentence: str) -> list[str]:
    return [part.strip(" .") for part in _CLAUSE_SPLIT.split(sentence) if part.strip(" .")]


def _missing_clauses(document: str) -> list[str]:
    """The card sentence's clauses that the caveat block does not carry, in order.

    Each clause is searched from where the previous one ended rather than in the whole block, so
    the three have to appear in the order the constant puts them. Asking only whether the section
    contains them somewhere was measurably weaker: a clause could be deleted from the bullet that
    quotes the sentence and parked in an unrelated bullet lower down, and the check stayed green.
    """
    block = _flat(_caveat_block(document))
    missing: list[str] = []
    cursor = 0
    for clause in _clauses(MESSAGE_STOP_START_REQUIRED):
        found = block.find(_flat(clause), cursor)
        if found == -1:
            missing.append(clause)
        else:
            cursor = found + len(_flat(clause))
    return missing


def test_the_card_sentence_has_the_clauses_this_test_looks_for() -> None:
    """The precondition, asserted rather than assumed.

    A constant rewritten into one unbroken clause would leave the check below comparing a single
    long string, and a constant that lost its punctuation entirely would leave it comparing the
    whole sentence as one clause — passing while checking far less than it reads as checking.
    """
    assert len(_clauses(MESSAGE_STOP_START_REQUIRED)) == 3


def test_the_caveat_block_quotes_every_clause_of_the_card_sentence() -> None:
    assert _missing_clauses(DOC.read_text(encoding="utf-8")) == []


def test_a_paraphrased_clause_is_caught() -> None:
    """The negative control, against a mutated copy.

    This is the exact drift that reached `main` unnoticed: the instruction restated in the
    document's own voice instead of quoted. It is asserted on a copy here so the check has a
    watched failure of its own; the real file was also paraphrased by hand once and watched go red.

    **The expected list below is a hand-kept copy of clause 3, deliberately, and it is the one
    place in this file that holds one.** The module docstring rejects a hand-kept list of expected
    sentences for the check itself, and that still stands — this is the control, not the check, and
    a control that derived its expectation the same way the check does would pass on the same bug.
    The cost is real and is the point: reword clause 3 in the constant and this test goes red until
    someone updates the copy, which is a second reader for a change to the owner's bytes.
    """
    document = DOC.read_text(encoding="utf-8")
    paraphrased = document.replace(
        "— **stop and start it from the customer portal or from Proxmox**",
        "so the stop and start is done from the customer portal or from Proxmox",
    )

    # `str.replace` is silent when its needle misses, and a miss would leave this asserting that
    # the unchanged document fails — the same red for the opposite reason, naming the wrong cause.
    assert paraphrased != document

    assert _missing_clauses(paraphrased) == [
        "stop and start it from the customer portal or from Proxmox"
    ]


def test_a_renamed_heading_fails_rather_than_passing_on_nothing() -> None:
    """The other quiet failure: no section found, no clauses missing, green forever."""
    with pytest.raises(AssertionError, match="no longer has the heading"):
        _caveat_block("# Proxmox\n\nNo caveats here.\n")
