"""Strip login/PAM/LVE banners from SSH command output (T14, V56).

Ported from `noa-old` branch `MCP` (`core/remote_exec/banner_strip.py`, C13/V69) unchanged
except for these cites — it was hardened against real hosts and the false-positive guard is
the expensive part.

Some hosts (notably CloudLinux servers reached over the non-root ``sudo`` path,
`noa-old` GH #82/#83) inject a boxed ``*`` PAM/LVE banner onto **stdout** before the real
command output, e.g.::

    ***************************************************************************
    *             !!!!  WARNING: YOU ARE INSIDE LVE !!!!                      *
    ***************************************************************************
    {"max_count": 0, ...}

That prefix breaks downstream parsers (``json.loads`` on Imunify ``--json``).
``strip_ssh_banners`` removes only **known, signature-gated** boxed banner blocks so
legitimate output that merely contains ``*`` lines or boxed text is never touched (V56).
"""

from __future__ import annotations

import re

# A border is a line consisting solely of 3+ asterisks (after trimming
# surrounding whitespace).
_BORDER_RE = re.compile(r"\*{3,}")

# A boxed block is removed only when its interior matches one of these known
# banner signatures. Extend this tuple for additional known banners (MOTD,
# other PAM modules) — keep each pattern specific to avoid false positives.
_KNOWN_BANNER_SIGNATURES: tuple[re.Pattern[str], ...] = (
    re.compile(r"YOU ARE INSIDE LVE", re.IGNORECASE),
)


def _is_border(line: str) -> bool:
    return bool(_BORDER_RE.fullmatch(line.strip()))


def _block_matches_signature(block: list[str]) -> bool:
    block_text = "\n".join(block)
    return any(sig.search(block_text) for sig in _KNOWN_BANNER_SIGNATURES)


def strip_ssh_banners(text: str) -> str:
    """Remove known boxed login/PAM/LVE banner blocks from ``text``.

    Conservative and signature-gated: a boxed ``*`` block is dropped only when
    its interior matches a known banner signature. Handles banners anywhere in
    the output and multiple banners. An unterminated block (opening border with
    no closing border) is kept verbatim. When nothing is removed the original
    string object is returned **unchanged** (byte-exact — no line-ending or
    trailing-newline normalisation).
    """
    if "*" not in text:
        return text

    lines = text.splitlines()
    result: list[str] = []
    removed = False
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]
        if not _is_border(line):
            result.append(line)
            index += 1
            continue

        # Opening border: seek the next border to bound a candidate block.
        closing = index + 1
        while closing < total and not _is_border(lines[closing]):
            closing += 1

        if closing >= total:
            # Unterminated block — keep everything from here verbatim.
            result.extend(lines[index:])
            break

        block = lines[index : closing + 1]
        if _block_matches_signature(block):
            removed = True
            index = closing + 1
            # Skip blank lines that immediately follow a stripped banner.
            while index < total and lines[index].strip() == "":
                index += 1
            continue

        # Not a known banner — keep the border line, re-examine the closing
        # border as a potential opening border for an adjacent block.
        result.append(line)
        index += 1

    if not removed:
        return text

    return "\n".join(result)


__all__ = ["strip_ssh_banners"]
