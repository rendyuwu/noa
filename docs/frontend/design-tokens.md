# Design tokens

## Source of truth
- `apps/noa/app/globals.css` owns the raw values and theme override blocks.
- `apps/noa/tailwind.config.ts` exposes those values as semantic Tailwind utilities.
- Shared UI variants in `apps/noa/components/ui/*` should consume the semantic utilities, not raw palette classes.

## Token layers
1. **Foundation tokens**: raw CSS variables for surfaces, text, borders, radii, shadow, and fonts.
2. **Semantic tokens**: meaning-based aliases like `bg`, `surface`, `text`, `muted`, `accent`, `success`, `warning`, `info`, `destructive`, and `overlay`.
3. **Component variants**: reusable UI contracts for repeated states such as badges, notices, and status surfaces.

## Rules
- Prefer semantic utilities in `app/**` and `components/**`.
- Do not add raw palette utilities or hard-coded color literals outside token/config files.
- Move repeated status styling into shared variants instead of duplicating class maps.
- Add future theme values in the same CSS file using `:root` and theme override blocks.
- Keep `npm run verify:design-system` green before merging.
