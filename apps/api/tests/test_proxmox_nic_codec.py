"""The `netN` codec — what a NIC line says, and what a rewrite of it must not lose (T28).

`core.integrations.proxmox.nic` has no tool context, no client and no session, so it is tested
here directly: the grammar is the whole subject, and the two halves of `proxmox_vm_nic` are two
callers of it rather than the thing under test (§V.66).

**Why a rewrite is the risky operation.** Proxmox takes a `netN` line whole. There is no "set the
`link_down` field" call — the change is a write of the entire value, so every segment this module
drops is a segment deleted from a live VM. Most of this file is that one property, asserted from
several directions, because a codec that loses `tag=42` produces a VM on the wrong VLAN and
answers `ok`.

The other thread is **Proxmox's truthiness, not Python's** (§V.87's family): `link_down=0` is a
non-empty string, and a codec that read it as set would report an enabled NIC as disabled.
"""

from __future__ import annotations

import pytest

from core.integrations.proxmox.nic import (
    LINK_STATE_DOWN,
    LINK_STATE_UP,
    NetworkInterface,
    find_nic,
    list_nics,
    net_has_link_down,
    net_link_state,
    parse_net_segments,
    read_nic,
    set_link_down,
)

ORDINARY = "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,firewall=1,tag=42"


# --- Reading a line ---


def test_segments_keep_their_order_and_a_valueless_one_is_none() -> None:
    """Order is kept because `set_link_down` writes the line back from these pairs."""
    assert parse_net_segments("virtio=AA:BB,bridge=vmbr0,link_down") == [
        ("virtio", "AA:BB"),
        ("bridge", "vmbr0"),
        ("link_down", None),
    ]


def test_an_empty_segment_is_dropped_rather_than_becoming_an_empty_key() -> None:
    """A trailing comma round-trips away instead of turning into a nameless segment."""
    assert parse_net_segments("virtio=AA:BB,,bridge=vmbr0,") == [
        ("virtio", "AA:BB"),
        ("bridge", "vmbr0"),
    ]


def test_a_value_containing_an_equals_sign_keeps_it() -> None:
    """Split on the *first* `=` only — a base64-ish option value carries its own."""
    assert parse_net_segments("virtio=AA:BB,opt=a=b") == [("virtio", "AA:BB"), ("opt", "a=b")]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("virtio=AA:BB", False, id="absent"),
        pytest.param("virtio=AA:BB,link_down=1", True, id="one"),
        pytest.param("virtio=AA:BB,link_down=0", False, id="zero-is-up"),
        pytest.param("virtio=AA:BB,link_down", True, id="valueless-flag-is-down"),
        pytest.param("virtio=AA:BB,link_down=true", True, id="true"),
        pytest.param("virtio=AA:BB,link_down=YES", True, id="case-folded"),
        pytest.param("virtio=AA:BB,link_down=on", True, id="on"),
        pytest.param("virtio=AA:BB,link_down=off", False, id="off-is-up"),
        pytest.param("virtio=AA:BB,link_down= 1 ", True, id="padded"),
    ],
)
def test_link_down_reads_proxmoxs_truthiness_not_pythons(value: str, expected: bool) -> None:
    """`link_down=0` is the case that matters: a non-empty string that means *up*.

    Reading it as set would report an enabled NIC as disabled, which is a card describing the
    opposite of the machine's state.
    """
    assert net_has_link_down(value) is expected


def test_link_state_names_the_two_words_the_rest_of_the_code_compares_on() -> None:
    assert net_link_state("virtio=AA:BB") == LINK_STATE_UP
    assert net_link_state("virtio=AA:BB,link_down=1") == LINK_STATE_DOWN


# --- Reading one NIC ---


def test_a_nic_is_read_by_the_shape_of_its_mac_not_by_segment_position() -> None:
    """The model/MAC segment is found by its value, which is `noa-old`'s bug corrected.

    That repo took `segments[0]` positionally. A hand-edited line leading with `bridge=` then
    reports `model="bridge"`, `mac_address="vmbr0"` — a made-up model name on an approval card.
    """
    nic = read_nic("net0", "bridge=vmbr0,virtio=AA:BB:CC:DD:EE:01,tag=42")

    assert nic.model == "virtio"
    assert nic.mac_address == "AA:BB:CC:DD:EE:01"
    assert nic.bridge == "vmbr0"


def test_a_line_with_no_mac_reports_no_model_rather_than_guessing_one() -> None:
    """An absence, not a value — the same rule §V.86 makes about an unanswered read."""
    nic = read_nic("net0", "bridge=vmbr0,firewall=1")

    assert nic.model is None
    assert nic.mac_address is None
    assert nic.bridge == "vmbr0"


def test_the_raw_line_is_kept_and_the_evidence_does_not_carry_it() -> None:
    """The runner edits the line it read; the card shows what an operator recognises.

    The raw value is the one field that grows without bound as Proxmox gains options, and the
    runner re-reads before it writes, so a copy on the evidence would be stale *and* useless.
    """
    nic = read_nic("net0", ORDINARY)

    assert nic.value == ORDINARY
    assert set(nic.as_evidence()) == {"net", "model", "mac_address", "bridge", "link_state"}


def test_a_choice_entry_is_all_strings_with_absences_rendered_as_a_dash() -> None:
    """`tool_failure(choices=…)` takes `dict[str, str]`, and a literal `None` in a picker reads
    as a value rather than as an absence (§V.18)."""
    choice = read_nic("net0", "bridge=vmbr0").as_choice()

    assert all(isinstance(value, str) for value in choice.values())
    assert choice["model"] == "-"
    assert choice["mac_address"] == "-"


def test_link_down_is_derived_from_the_state_rather_than_stored_beside_it() -> None:
    """Two fields that can disagree are one field too many (`cloudinit`'s `verified`, one over)."""
    assert read_nic("net0", ORDINARY).link_down is False
    assert read_nic("net0", f"{ORDINARY},link_down=1").link_down is True


# --- Rewriting a line ---


def test_disabling_keeps_every_segment_and_appends_the_flag() -> None:
    """The property this whole file exists for: a rewrite is a whole-line write.

    `tag=42` is the one to watch — dropping it moves a live VM onto a different VLAN, and the
    tool would still answer `ok`.
    """
    assert (
        set_link_down(ORDINARY, disabled=True)
        == "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,firewall=1,tag=42,link_down=1"
    )


def test_enabling_removes_the_flag_rather_than_writing_zero() -> None:
    """Equivalent to Proxmox, and it makes a never-disabled NIC and a re-enabled one identical."""
    assert set_link_down(f"{ORDINARY},link_down=1", disabled=False) == ORDINARY


def test_disabling_a_line_that_is_already_down_edits_in_place_and_does_not_move_it() -> None:
    """One `link_down` in the result however many the input had, in the position it was in."""
    assert (
        set_link_down("virtio=AA:BB,link_down=1,bridge=vmbr0", disabled=True)
        == "virtio=AA:BB,link_down=1,bridge=vmbr0"
    )


def test_a_valueless_flag_is_normalised_to_one_rather_than_left_bare() -> None:
    """What NOA writes is always the explicit form, whatever it read."""
    assert set_link_down("virtio=AA:BB,link_down", disabled=True) == "virtio=AA:BB,link_down=1"


def test_a_duplicated_flag_collapses_to_one() -> None:
    """A config Proxmox accepted cannot carry two, so this is a rule for a line nothing sane
    produced — and it is what keeps the codec's own round trip stable."""
    assert (
        set_link_down("virtio=AA:BB,link_down=1,tag=7,link_down=0", disabled=True)
        == "virtio=AA:BB,link_down=1,tag=7"
    )
    assert (
        set_link_down("virtio=AA:BB,link_down=1,tag=7,link_down=0", disabled=False)
        == "virtio=AA:BB,tag=7"
    )


@pytest.mark.parametrize("disabled", [True, False], ids=["down", "up"])
def test_a_rewrite_round_trips_to_the_state_that_was_asked_for(disabled: bool) -> None:
    """The codec's own contract, and the assertion the runner's postflight rests on."""
    rewritten = set_link_down(ORDINARY, disabled=disabled)

    assert net_has_link_down(rewritten) is disabled
    assert set_link_down(rewritten, disabled=disabled) == rewritten


# --- Listing a config's NICs ---


def test_nics_are_listed_in_proxmoxs_own_numeric_order() -> None:
    """Numeric, not lexical: `net2` before `net10`.

    An operator reads this as a `choices` list, and the order is reproducible across identical
    calls (§V.85's ordering rule, on a list too small to need a cap).
    """
    config = {"net10": "virtio=AA:0A", "net2": "virtio=AA:02", "net0": "virtio=AA:00"}

    assert [nic.key for nic in list_nics(config)] == ["net0", "net2", "net10"]


def test_a_key_that_only_looks_like_a_nic_is_not_one() -> None:
    """Anchored on both ends. A substring match would offer `netfoo` as an interface to disable."""
    config = {
        "net0": "virtio=AA:00",
        "net10x": "virtio=AA:0A",
        "network": "something",
        "netdev0": "virtio=AA:0B",
        "name": "web-01",
    }

    assert [nic.key for nic in list_nics(config)] == ["net0"]


def test_a_non_string_or_blank_value_is_skipped_rather_than_stringified() -> None:
    """A config key that is not a line NOA can parse is not a NIC it can offer to change."""
    config = {"net0": "virtio=AA:00", "net1": None, "net2": 5, "net3": "   "}

    assert [nic.key for nic in list_nics(config)] == ["net0"]


def test_an_empty_config_lists_nothing_rather_than_raising() -> None:
    """The `no_nics_found` refusal is the tool's to make, off an empty list."""
    assert list_nics({}) == []


def test_find_nic_is_exact_because_proxmox_is() -> None:
    """`net0` is not `NET0` to Proxmox, and a case-folding match would edit a neighbour."""
    nics = list_nics({"net0": "virtio=AA:00", "net1": "virtio=AA:01"})

    found = find_nic(nics, "net1")
    assert isinstance(found, NetworkInterface)
    assert found.key == "net1"
    assert find_nic(nics, "NET1") is None
    assert find_nic(nics, "net2") is None
