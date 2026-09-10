"""Combined-stream view of a `CommandResult`.

`noa-old` carried this function **three times**, byte-identical, on branch `MCP`:
`whm/integrations/csf_cli.py:30`, `whm/integrations/imunify_cli.py:33`, and
`pmg/integrations/pmgsh_cli.py:30`. It lands here with T16 rather than a fourth time, because
T18 (PMG) is the fourth caller and V66 puts shared code in `core/`. Nothing about it is
WHM-specific — it reads `CommandResult`, which is this package's type.

Why both streams: the CLIs it serves are inconsistent about which one they use. `csf -g` writes
its tables to stdout but its usage errors to stderr; `imunify360-agent` puts a failed command's
explanation on stderr while `--json` output goes to stdout. A caller that reads one stream gets
an empty error message roughly half the time, which is how a failure ends up reported as
`… failed with exit code 1` and nothing else.

**Reads `stdout`, ⊥ `raw_stdout`.** This text is parsed and shown to an operator, so it
must be the banner-stripped stream — a CloudLinux LVE banner in front of a JSON document is
exactly what `noa-old` GH #83 was. `raw_stdout`/`raw_stderr` stay on the result for audit.
"""

from __future__ import annotations

from core.remote_exec.types import CommandResult


def command_output_text(result: CommandResult) -> str:
    """Stripped stdout and stderr, joined by a newline, with blank streams omitted."""
    parts: list[str] = []
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    return "\n".join(parts).strip()


__all__ = ["command_output_text"]
