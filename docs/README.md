# docs

Integration and operations reference.

- `integrations/` — one file per external system (WHM, Proxmox, PMG, yopass). Ports land with their
  tasks: yopass doc with §T.15, WHM with §T.16, Proxmox with §T.17, PMG with §T.18.

Rule carried over from the old repo: when a WHM, Proxmox, or PMG feature changes, update the
matching file in `integrations/` in the same change.

Design rationale lives in `DECISIONS.md` and `ARCHITECTURE.md` (§T.62), not here.
