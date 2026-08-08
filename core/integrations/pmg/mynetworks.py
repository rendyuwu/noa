"""`pmgsh ls /config/mynetworks` output → normalised CIDR entries (T31, V58, V59).

The half of `noa-old`'s `pmg/tools/whitelist_tools.py` that is not a tool: T18 ported the
command layer and named this as deferred (`core.integrations.pmg.__init__`), because parsing
belongs beside the commands rather than inside whichever tool ran one. `pmg_whitelist_search`
(T31), `pmg_whitelist_list` (T30) and `pmg_whitelist` (T29) all read the same text.

**V59 is one rule applied twice.** A single host and its host route are the same whitelist
entry — `1.2.3.4` ≡ `1.2.3.4/32`, `2001:db8::1` ≡ `2001:db8::1/128` — so both the operator's
target and every stored line go through `normalize_cidr` before anything is compared. Matching
raw strings instead would report "not whitelisted" for an address PMG whitelists, in the exact
spelling PMG happens to use.

`ipaddress.ip_network(value, strict=False)` is the single normalisation call. `noa-old` tried
`ip_address` first and fell back to `ip_network`, which is the same answer twice: for every
string `ip_address` accepts, `ip_network(value, strict=False)` yields `value/max_prefixlen`.
`strict=False` also masks host bits, so `1.2.3.4/24` normalises to `1.2.3.0/24` — the ported
behaviour, and the reason a caller reports the normalised form beside its verdict instead of
echoing what it was handed.

## Reading the output

`pmgsh ls` prints `<id> <cidr>` rows under an `id cidr` header, with a `200 OK` status line
somewhere in it:

    200 OK
    id cidr
    1 10.10.10.0/24
    2 103.150.86.115/32

Per line the second column is tried, then the first — `noa-old`'s order, which tolerates a
bare-CIDR variant of the output. Nothing else is needed to skip the noise: `200`, `OK`, `id`
and `cidr` all fail to parse as an address or a network, so status and header lines produce no
entries. (`ipaddress` rejects a bare decimal *string* such as `"200"`; it accepts an `int`, and
nothing here passes one.)

Two departures from `noa-old`, both deliberate (T21 (b): a port carries the code, not the
defect):

- **The first parsable candidate ends the line.** `noa-old` skipped a candidate whose
  normalised form it had already seen and fell through to the *next column*, so a repeated CIDR
  could make the id column get parsed in its place.
- **Duplicates are kept.** `noa-old` deduplicated by normalised form while parsing. Two
  spellings of one address in `mynetworks` are a fact about the whitelist, and a search that
  drops one reports a list its own source does not have. A caller that wants unique CIDRs (T30)
  can collapse them where the collapsing is visible.

Entries keep `cidr` — the token exactly as PMG printed it — beside `normalized`. That is the
evidence an operator reads: the two differ whenever the whitelist stores a bare host, and only
the raw form tells them what is actually in the file.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True)
class MynetworksEntry:
    """One `mynetworks` line NOA could read as an address or a network.

    `cidr` is PMG's own spelling and `normalized` is what membership is decided on (V59).
    Both travel, because a verdict computed on the normalised form has to be justifiable
    against the text the operator would see on the box.
    """

    cidr: str
    normalized: str

    def as_payload(self) -> dict[str, str]:
        """The entry as a tool result carries it. No line numbers, no raw line (V26)."""
        return {"cidr": self.cidr, "normalized": self.normalized}


def normalize_cidr(value: str) -> str | None:
    """`value` as a canonical CIDR string, or `None` when it is not an address or network.

    `None` rather than a raise: every caller is either classifying an operator's target (a
    refusal the model can act on, V18/V19) or skipping a line of command output that was never
    meant to be an address.
    """
    try:
        return str(ipaddress.ip_network(value.strip(), strict=False))
    except ValueError:
        return None


def parse_mynetworks_entries(output: str) -> list[MynetworksEntry]:
    """Every whitelist entry in `pmgsh ls /config/mynetworks` output, in the order PMG printed.

    Order is PMG's own and is not re-sorted: the caller reports membership, not a listing, and
    an entry's position is the one thing about it this layer cannot improve on.
    """
    entries: list[MynetworksEntry] = []
    for line in output.splitlines():
        columns = line.strip().split()
        # `<id> <cidr>` first, then the bare-CIDR variant. The first candidate that parses
        # ends the line — falling through would let the id column stand in for the address.
        for candidate in columns[1:2] + columns[:1]:
            normalized = normalize_cidr(candidate)
            if normalized is not None:
                entries.append(MynetworksEntry(cidr=candidate, normalized=normalized))
                break
    return entries


def find_matching_entries(
    entries: list[MynetworksEntry], *, normalized_target: str
) -> list[MynetworksEntry]:
    """The entries that *are* `normalized_target` (V59) — exact membership, ⊥ containment.

    A containing network is not a match: `1.2.3.0/24` in `mynetworks` does not make
    `1.2.3.4/32` an entry, and answering otherwise would tell an operator their address is
    already whitelisted when removing it would take a different CIDR than the one they named.
    Deciding containment is a policy question PMG's own config does not encode.
    """
    return [entry for entry in entries if entry.normalized == normalized_target]


__all__ = [
    "MynetworksEntry",
    "find_matching_entries",
    "normalize_cidr",
    "parse_mynetworks_entries",
]
