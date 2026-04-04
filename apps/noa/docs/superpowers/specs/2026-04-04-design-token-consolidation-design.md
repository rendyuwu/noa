# apps/noa Design Token Consolidation Design

## Goal
Consolidate the `apps/noa` design token system so feature code uses semantic tokens and shared variants instead of raw color utilities, while preserving existing component APIs in the first phase and creating a safe path to broader design-system consistency, dark mode readiness, and future theme expansion.

## Scope
- In scope: `apps/noa` only
- In scope: token definitions, Tailwind token mapping, shared UI variants, assistant/admin/login styling cleanup, local enforcement guardrails
- In scope: opportunistic cleanup of repeated spacing/radius/typography arbitrary values when the replacement is clearly more consistent
- Out of scope: repo-wide design token standard, unrelated visual redesign, non-frontend apps

## Current state
The audited code already has a real token foundation:
- `app/globals.css` defines centralized CSS custom properties for background, surfaces, borders, text, accent, radii, shadow, and fonts
- `tailwind.config.ts` maps those tokens into semantic Tailwind utilities
- core shared primitives such as `components/ui/button.tsx` consume semantic token utilities correctly

The main weakness is inconsistent consumption at the feature layer:
- assistant and admin components frequently use raw Tailwind palette classes such as `bg-amber-100`, `text-red-900`, `bg-emerald-50`, and `bg-sky-100`
- `components/ui/badge.tsx` mixes semantic variants with a hardcoded `success` variant
- overlays and some exception states use direct `bg-black/...`, `bg-white`, and similar raw values
- no dark-mode or multi-theme override structure currently exists (`.dark`, `data-theme`, `ThemeProvider`, or `prefers-color-scheme` were not found)

This means the architecture is centralized at the token-definition layer, but not yet enforced at the usage layer.

## Design goals
1. Make semantic tokens the only normal way to express color meaning in `apps/noa`
2. Preserve existing component APIs during the first phase
3. Reduce duplicated state styling logic across assistant/admin features
4. Block future drift with a local validation script
5. Leave the app structurally ready for dark mode or multiple themes
6. Improve consistency for repeated spacing/radius/type values where practical without speculative redesign

## Non-goals
- Full visual redesign of NOA
- Introducing a repo-level design system shared by other apps
- Replacing Tailwind or shadcn/ui
- Reworking large feature flows solely for stylistic reasons

## Target architecture

### 1. Token layering
The app should use a three-layer model.

#### Foundation tokens
Defined in `app/globals.css`.

These are low-level values such as:
- surfaces and background tokens
- text and border tokens
- accent/focus tokens
- spacing/radius/shadow/font scale tokens

These tokens store raw values and should remain the single source of truth.

#### Semantic tokens
Also defined in `app/globals.css`, then exposed through `tailwind.config.ts`.

These express meaning rather than palette identity. Examples:
- `primary`
- `secondary`
- `success`
- `warning`
- `info`
- `destructive`
- `overlay`
- `foreground-muted`
- `surface-muted`

Feature code should depend on semantic meaning only.

#### Component variants
Defined in shared UI files such as `components/ui/badge.tsx` and any new shared alert/status helpers.

These variants consume semantic tokens and provide reusable styling contracts. Feature code should use these instead of repeating status class maps.

### 2. Source of truth
- `app/globals.css` remains the canonical token source
- `tailwind.config.ts` remains the semantic utility bridge
- feature files in `app/**` and `components/**` should not define color meaning with raw palette classes

### 3. Theming model
The system should remain single-theme in behavior initially, but the CSS variable layout should be expanded so theme overrides can be added later without refactoring feature code.

The future-ready structure is:
- base theme in `:root`
- future theme override blocks in the same file, such as `.dark` or `[data-theme="..."]`

This work does not require dark mode to ship immediately, but it must make dark mode realistic.

## Phased rollout

### Phase 1: safe semantic migration
Primary objective: remove the highest-risk token inconsistencies without breaking component APIs.

#### Changes
- extend `app/globals.css` with missing semantic color families for repeated status meanings:
  - success
  - success-foreground
  - warning
  - warning-foreground
  - info
  - info-foreground
  - overlay
- add any clearly repeated neutral semantic aliases needed for current usage
- extend `tailwind.config.ts` to expose those semantic tokens as Tailwind utilities
- replace raw status color classes in feature code with semantic utilities
- replace raw black/white styling in app code with semantic token equivalents where practical
- normalize repeated arbitrary values for spacing/radius/typography when they are repeated enough to justify standardization and the replacement is obvious
- add a local validation script that blocks new banned raw palette usage in `apps/noa`

#### Constraints
- preserve existing public component APIs where possible
- do not require full feature rewrites
- keep changes scoped to `apps/noa`

#### Success criteria
- high-priority raw palette usage is removed from assistant/admin/login flows
- shared UI `Badge` no longer hardcodes `success`
- new semantic token families are available through Tailwind
- a validation script can fail the build/check when banned raw color classes are introduced

### Phase 2: shared component consolidation
Primary objective: reduce duplication and drift.

#### Changes
- centralize repeated status, alert, and badge patterns into shared variants/helpers
- extract repeated state-to-style maps where multiple features currently duplicate them
- update feature code to consume shared variants instead of embedding status class strings

#### Success criteria
- repeated assistant/admin state styling is expressed through shared abstractions
- duplicated raw styling logic is removed from feature files
- component-level semantics are easier to maintain consistently

### Phase 3: theme readiness
Primary objective: make dark mode or multiple themes structurally maintainable.

#### Changes
- add theme override structure to `app/globals.css`
- ensure overlays, status colors, neutral surfaces, and state styles all resolve through tokens
- finish any remaining migration blockers preventing theme swaps

#### Success criteria
- feature code is no longer directly color-aware
- semantic token overrides are sufficient to change theme appearance without touching feature files
- dark-mode work becomes mostly a token-definition task rather than a codebase-wide refactor

### Safe stopping points
- after Phase 1: token usage is centralized enough to enforce and continue safely
- after Phase 2: repeated UI patterns are consolidated and drift is reduced
- after Phase 3: multi-theme expansion is practical

## Enforcement model

### Policy
Treat raw Tailwind palette classes in `app/**` and `components/**` as violations, except for tightly-scoped framework exceptions if explicitly allowlisted.

Blocked examples include:
- `bg-red-50`
- `text-amber-900`
- `border-emerald-200`
- `ring-sky-300`
- `bg-white`
- `text-black`
- arbitrary color utilities such as `bg-[#fff]`, `text-[rgb(...)]`, `border-[hsl(...)]`

This policy is intentionally strict. It is meant to force semantic-token use rather than allow gradual drift to continue.

### Implementation
Add a custom check script under `apps/noa/scripts/` and wire it into `package.json` as a validation command.

The script should:
- scan `app/**` and `components/**`
- ignore token/config definition files such as `app/globals.css` and `tailwind.config.ts`
- detect banned raw color utility patterns
- exit non-zero with readable file/line output when violations are found

### Guardrail philosophy
The guardrail should block drift immediately, but remain simple and local to this app. A custom script is preferred over introducing broader lint infrastructure at this stage.

## File responsibilities

### Core token files
- `app/globals.css`
  - canonical token definitions
  - current theme values
  - future theme override blocks
- `tailwind.config.ts`
  - semantic token exposure for utility use

### Shared UI layer
- `components/ui/badge.tsx`
  - semantic badge variants only
- new or updated shared alert/status files under `components/ui/`
  - reusable state and notice presentation contracts

### High-priority migration targets
- `components/assistant/workflow-todo-tool-ui.tsx`
- `components/assistant/approval-dock.tsx`
- `components/assistant/workflow-dock.tsx`
- `components/assistant/workflow-receipt-renderer.tsx`
- `components/assistant/assistant-thread-panel.tsx`

### Follow-up migration targets
- `components/admin/whm-servers-admin-page.tsx`
- `components/admin/proxmox-servers-admin-page.tsx`
- `components/admin/audit-admin-page.tsx`
- `components/admin/audit-receipt-page.tsx`
- `components/admin/users/users-list-panel.tsx`
- `components/admin/roles/role-tools-panel.tsx`
- `components/admin/roles/roles-list-panel.tsx`
- `app/login/page.tsx`

### Validation/automation files
- new script under `scripts/`
- `package.json`
  - add a command for the token enforcement check

## Migration rules
1. No raw palette meaning in feature code
2. Feature code should consume semantic utilities or shared variants only
3. If the same status styling appears in two or more places, move it into a shared abstraction
4. Opportunistic cleanup of repeated arbitrary spacing/radius/typography values is allowed when it improves consistency without broad redesign
5. Preserve behavior first; consolidate APIs later if needed

## Testing and verification strategy

### Required validation
- run the custom style guardrail script
- run the app typecheck/build commands already used in this repo
- run any targeted tests affected by extracted shared variant logic

### Expected verification evidence
- no banned raw color utilities remain in targeted files
- semantic token classes resolve correctly in shared primitives and migrated features
- build/typecheck still pass

## Risks and mitigations

### Risk: over-normalizing arbitrary values
Mitigation: only standardize repeated values that clearly represent an existing reusable scale.

### Risk: introducing premature abstraction in Phase 1
Mitigation: preserve component APIs and prefer direct semantic replacements before extraction.

### Risk: guardrail false positives
Mitigation: keep the script scope narrow, ignore token/config files, and use a small explicit allowlist only when unavoidable.

### Risk: theme-readiness work expands scope
Mitigation: Phase 3 is structural preparation, not a mandatory full dark-mode rollout.

## Rollout summary
This design uses a phased semantic-token migration as the recommended path:
- Phase 1 creates enforceable semantic consistency with minimal API disruption
- Phase 2 removes repeated state styling logic by consolidating shared variants
- Phase 3 completes the architecture needed for dark mode or multiple themes

This path matches the current maturity of `apps/noa`: the foundation is already centralized, but the feature layer still needs migration and enforcement.
