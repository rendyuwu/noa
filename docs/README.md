# docs

Integration and operations reference.

- `integrations/` — one file per external system (WHM, Proxmox, PMG, yopass, LibreChat). Ports land
  with their tasks: yopass doc with §T.15, WHM with §T.16, Proxmox with §T.17, PMG with §T.18.
  `librechat.md` is not a port — it is the operator config reference for the sole MCP client
  (§T.57) plus the example agent system prompt (§T.58), and its config keys are asserted by
  `apps/api/tests/test_librechat_config_doc.py` rather than merely written down (§V.88).
- `admin-web.md` — the BIGSU admin panel: what was ported and what deviates, BIGSU registry access
  and the internal-runner requirement it forces on CI, config and health (§T.47, §T.48).
- `deployment.md` — the three images and their build contexts, build-time versus runtime
  configuration, the domain and session-cookie layout, the local compose stack, and the
  single-replica and readiness decisions (§T.60). Its machine-readable blocks are asserted by
  `apps/api/tests/test_deployment.py` rather than merely written down.

Rule carried over from the old repo: when a WHM, Proxmox, or PMG feature changes, update the
matching file in `integrations/` in the same change.

Design rationale lives in `DECISIONS.md` and `ARCHITECTURE.md` (dependency pins §T.70, the rest with
§T.62), not here.
