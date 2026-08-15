# docs

Integration and operations reference.

- `integrations/` — one file per external system (WHM, Proxmox, PMG, yopass). Ports land with their
  tasks: yopass doc with §T.15, WHM with §T.16, Proxmox with §T.17, PMG with §T.18.
- `admin-web.md` — the BIGSU admin panel: what was ported and what deviates, BIGSU registry access
  and the internal-runner requirement it forces on CI, config and health (§T.47, §T.48).

Rule carried over from the old repo: when a WHM, Proxmox, or PMG feature changes, update the
matching file in `integrations/` in the same change.

Design rationale lives in `DECISIONS.md` and `ARCHITECTURE.md` (dependency pins §T.70, the rest with
§T.62), not here.
