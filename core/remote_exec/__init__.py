"""Remote command execution over SSH.

Copied from `noa-old` branch `MCP` rather than rewritten: banner stripping,
`sudo -n` escalation, host-key pinning and TOFU refresh were each paid for in a production
incident, and the false-positive guards are the expensive part of all four.

Four modules, one job each:

- `types`        — `SSHConnectionConfig` (host + credentials + the pin), `CommandResult`
                   (parsed *and* raw streams — banners stripped before parsing, raw kept for audit).
- `errors`       — `SSHExecutionError`, a `NoaError` so the one shared handler shapes it.
- `banner_strip` — signature-gated removal of CloudLinux LVE/PAM banners.
- `ssh`          — pinned `ssh_exec`, TOFU `ssh_get_host_fingerprint`, `command_from_argv`.
- `sudo`         — `sudo -n` iff the resolved user is not root, and the
                   rights-failure classifier that makes `ssh_sudo_required` distinct.
- `output`       — `command_output_text`, the both-streams view every CLI wrapper needs.
                   Added with the WHM layer, which is where the third copy of it would have landed.

Consumers: WHM csf/imunify, Proxmox, PMG `pmgsh`, and the admin
server-validate endpoints, which is where `ssh_get_host_fingerprint` earns its place.
"""
