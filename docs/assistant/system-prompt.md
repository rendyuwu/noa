# System Prompt

NOA loads its system prompt from a bundled markdown file instead of an inline config string.

## Update the default prompt

- Edit `apps/api/src/noa_api/core/prompts/noa-system-prompt.md`.
- Keep the prompt rules-first and operationally specific.
- Preserve workflow-family structured reply semantics when `replyTemplate` data is present; keep outcome first, evidence second, and next safe step last.
- Add or update tests in `apps/api/tests/test_system_prompt_loader.py` when behavior changes.

## Override by environment

Highest precedence wins:

1. `LLM_SYSTEM_PROMPT`
2. `LLM_SYSTEM_PROMPT_PATH`
3. `LLM_SYSTEM_PROMPT_EXTRA_PATHS`
4. Bundled default prompt file

Behavior details:

- `LLM_SYSTEM_PROMPT` replaces every other prompt source.
- `LLM_SYSTEM_PROMPT_PATH` replaces the bundled default prompt file.
- `LLM_SYSTEM_PROMPT_EXTRA_PATHS` appends extra instruction files in order, separated with `---`.
- NOA logs a short prompt fingerprint at API startup for observability.

Example overrides:

```bash
LLM_SYSTEM_PROMPT_PATH=/etc/noa/prompts/prod-system-prompt.md
LLM_SYSTEM_PROMPT_EXTRA_PATHS=/etc/noa/prompts/org-rules.md,/etc/noa/prompts/team-rules.md
```

## Security guidance

- Never put secrets, credentials, tokens, or private keys in prompt files.
- Treat prompt files like code: review them, version them, and keep changes scoped.
- Per-request `system` and `tools` payload fields are ignored by the API and should not be used for production prompt control.
