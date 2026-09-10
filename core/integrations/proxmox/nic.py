"""What a Proxmox `netN` config line says, and how to flip its link state.

Ported — copied, not rewritten — from `noa-old` branch `MCP` (`proxmox/tools/nic_tools.py`), where
these were private helpers inside the tool module. They live in the integration layer here for two
reasons that both point the same way: the `netN` grammar is a **Proxmox** fact rather than a
workflow's, and `proxmox_vm_nic`'s two halves — the tool that reads the before-state and the runner
that writes after an approval — each need it, so a copy in either module is duplication — one
helper, not two.

**The format.** One `netN` value is a comma-separated list of `key=value` segments, with the
model and MAC address fused into the first one:

    virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=1,tag=42,link_down=1

The pieces NOA reads are the model, the MAC, the bridge and `link_down`. Everything else is
carried through untouched, which is not politeness — a rewrite is a **whole-line write** to
Proxmox, so a segment this module drops is a segment deleted from the VM.

**`set_link_down` rewrites in place.** It keeps segment order, keeps every segment it does not
recognise, and never emits a second `link_down` — a value that already carries one is edited
rather than appended to. Enabling **removes** the key rather than writing `link_down=0`: those are
equivalent to Proxmox, and the removal is what makes a NIC that was never disabled and one that
was re-enabled read identically afterwards.

**Truthiness is Proxmox's, not Python's.** `link_down=0` is *up*; a bare `link_down` with no value
is *down*, because that is how Proxmox's own option parser reads a valueless flag. Reading `"0"`
as a non-empty string — true — would report an enabled NIC as disabled, so the accepted words are
listed rather than inferred.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

# `net0`…`net31`. Anchored on both ends: `net10x` and `network` are not NIC keys, and a substring
# match would put a `netfoo` line in front of an operator as something they can disable.
NET_KEY_PATTERN: Final = re.compile(r"^net\d+$")

# How the model/MAC segment is told apart from an ordinary option — see `read_nic`. Six
# colon-separated octets; Proxmox writes them upper-case and accepts either.
MAC_ADDRESS_PATTERN: Final = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")

# The segment whose presence disables the link.
LINK_DOWN_SEGMENT: Final = "link_down"

# What Proxmox reads as a set flag. A valueless `link_down` is also set — see the module
# docstring — and `"0"` is deliberately absent, which is the whole point of listing them.
TRUE_WORDS: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

LINK_STATE_UP: Final = "up"
LINK_STATE_DOWN: Final = "down"


@dataclass(frozen=True)
class NetworkInterface:
    """One `netN` line, read.

    `value` is the raw line, kept because a rewrite is a whole-line write and the runner has to
    edit the line it actually read rather than one reassembled from these fields. The rest is what
    an operator recognises a NIC by on the approval card, and what a `choices` list has to carry
    for them to name one.
    """

    key: str
    value: str
    model: str | None
    mac_address: str | None
    bridge: str | None
    link_state: str

    @property
    def link_down(self) -> bool:
        """Is this NIC's link currently down?"""
        return self.link_state == LINK_STATE_DOWN

    def as_evidence(self) -> dict[str, Any]:
        """JSON-native, for `approval_context` JSONB, written by the gate that opens the request.

        The raw `value` is **not** here. It is the one field that grows without bound as Proxmox
        gains options, it means nothing to an operator reading a card, and the runner re-reads the
        line it is about to edit anyway (the NIC tool's digest decision) — so carrying it would be
        a stale copy of a value nothing consults.
        """
        return {
            "net": self.key,
            "model": self.model,
            "mac_address": self.mac_address,
            "bridge": self.bridge,
            "link_state": self.link_state,
        }

    def as_choice(self) -> dict[str, str]:
        """One candidate NIC, as a `choices` entry.

        All-`str` because that is `tool_failure`'s shape, and `-` rather than `None` for an absent
        field: this is read by a model and then by an operator, and a literal `None` in a picker
        reads as a value rather than as an absence.
        """
        return {
            "net": self.key,
            "model": self.model or "-",
            "mac_address": self.mac_address or "-",
            "bridge": self.bridge or "-",
            "link_state": self.link_state,
        }


def parse_net_segments(value: str) -> list[tuple[str, str | None]]:
    """One `netN` value as ordered `(key, value)` pairs; a valueless segment yields `None`.

    Empty segments are dropped, so a trailing comma round-trips away rather than becoming an
    empty key. Order is preserved because `set_link_down` writes the line back.
    """
    segments: list[tuple[str, str | None]] = []
    for raw_segment in value.split(","):
        segment = raw_segment.strip()
        if not segment:
            continue
        if "=" in segment:
            key, _, segment_value = segment.partition("=")
            segments.append((key.strip(), segment_value.strip()))
            continue
        segments.append((segment, None))
    return segments


def net_has_link_down(value: str) -> bool:
    """Is `link_down` set on this line? (Proxmox's truthiness, not Python's.)

    The **last** `link_down` wins. A config Proxmox accepted cannot carry two — its own property
    parser refuses a duplicate key — so this is a rule for a line nothing sane produced, and it is
    the one that makes `net_has_link_down(set_link_down(v, …))` agree with what was asked for,
    since `set_link_down` collapses the key to a single segment.
    """
    set_flag = False
    for key, segment_value in parse_net_segments(value):
        if key != LINK_DOWN_SEGMENT:
            continue
        set_flag = segment_value is None or segment_value.strip().lower() in TRUE_WORDS
    return set_flag


def net_link_state(value: str) -> str:
    """`"up"` or `"down"` for one `netN` line."""
    return LINK_STATE_DOWN if net_has_link_down(value) else LINK_STATE_UP


def set_link_down(value: str, *, disabled: bool) -> str:
    """Rewrite one `netN` line with the link down (`disabled`) or up.

    In place: segment order kept, unrecognised segments kept, and at most one `link_down` in the
    result however many the input had. Enabling removes the segment rather than writing
    `link_down=0` — equivalent to Proxmox, and it makes a never-disabled NIC and a re-enabled one
    read identically (see the module docstring).

    The result is what goes on the wire, so this is the only place a `netN` line is assembled.
    """
    rewritten: list[str] = []
    link_down_written = False

    for key, segment_value in parse_net_segments(value):
        if key == LINK_DOWN_SEGMENT:
            if disabled and not link_down_written:
                rewritten.append(f"{LINK_DOWN_SEGMENT}=1")
                link_down_written = True
            continue
        rewritten.append(key if segment_value is None else f"{key}={segment_value}")

    if disabled and not link_down_written:
        rewritten.append(f"{LINK_DOWN_SEGMENT}=1")

    return ",".join(rewritten)


def read_nic(key: str, value: str) -> NetworkInterface:
    """One config entry as a `NetworkInterface`.

    The model and MAC are fused into one segment by Proxmox (`virtio=AA:BB:CC:DD:EE:FF`), and the
    segment is found **by the shape of its value** rather than by being first. `noa-old` took
    `segments[0]` positionally, which reports `model="bridge"`, `mac_address="vmbr0"` for a
    hand-edited line that happens to lead with `bridge=` — a made-up model name on an approval
    card. A MAC address is recognisable and an option value is not, so there is no need to guess.

    A line with no MAC-shaped segment yields `model=None`, which the card and the `choices` list
    both render as an absence rather than as a value — never folding a non-answer into a benign
    default, one field down.
    """
    segments = parse_net_segments(value)
    model: str | None = None
    mac_address: str | None = None
    bridge: str | None = None

    for segment_key, segment_value in segments:
        if segment_value is not None and MAC_ADDRESS_PATTERN.fullmatch(segment_value):
            model, mac_address = segment_key, segment_value
            break

    for segment_key, segment_value in segments:
        if segment_key == "bridge":
            bridge = segment_value
            break

    return NetworkInterface(
        key=key,
        value=value,
        model=model,
        mac_address=mac_address,
        bridge=bridge,
        link_state=net_link_state(value),
    )


def list_nics(config: Mapping[str, Any]) -> list[NetworkInterface]:
    """Every QEMU NIC on one VM config, ordered by `netN` number.

    Sorted numerically rather than lexically, so a VM with ten NICs lists `net2` before `net10` —
    an operator reading a `choices` list sees Proxmox's own order, and the order is reproducible
    across identical calls — the same stable-order rule capped reads use, here on a list too small
    to cap.

    Non-string and blank values are skipped rather than stringified: a config key that is not a
    line NOA can parse is not a NIC it can offer to change.
    """
    nics: list[NetworkInterface] = []
    for key in sorted(
        (key for key in config if NET_KEY_PATTERN.fullmatch(key)),
        key=lambda key: int(key[3:]),
    ):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        nics.append(read_nic(key, value.strip()))
    return nics


def find_nic(nics: list[NetworkInterface], key: str) -> NetworkInterface | None:
    """The NIC named `key`, or `None`. Exact match — `net0` is not `NET0` to Proxmox."""
    for nic in nics:
        if nic.key == key:
            return nic
    return None


__all__ = [
    "LINK_DOWN_SEGMENT",
    "LINK_STATE_DOWN",
    "LINK_STATE_UP",
    "MAC_ADDRESS_PATTERN",
    "NET_KEY_PATTERN",
    "TRUE_WORDS",
    "NetworkInterface",
    "find_nic",
    "list_nics",
    "net_has_link_down",
    "net_link_state",
    "parse_net_segments",
    "read_nic",
    "set_link_down",
]
