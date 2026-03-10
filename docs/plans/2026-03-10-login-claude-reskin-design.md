# Login Page Claude Reskin (Design)

**Goal:** Reskin `apps/web/app/login/page.tsx` to match the Claude visual language (warm neutrals, serif-forward typography, subtle borders/shadows, orange primary) while keeping the existing login/auth logic exactly the same.

## Constraints

- Only change the UI layer in `apps/web/app/login/page.tsx` (no changes to API calls, auth-store, or routing).
- Remove inline styles and stop using legacy `.panel`, `.button`, `.button-primary`, `.input` classes; use Tailwind classes consistent with the existing Claude-styled `/assistant` UI.
- Accessibility: explicit labels, strong `focus-visible` affordances, and an announced error region.

## Visual Spec

- **Layout:** Full-page centered card using Tailwind (`min-h-dvh` + centered flex) for consistent spacing on mobile and desktop.
- **Card:** Warm paper-like surface with subtle border and shadow, slight translucency (`bg-white/70`) + `backdrop-blur-sm` to sit well on the warm gradient background.
- **Typography:** Claude-minimal header: serif `Login` heading with muted subtitle. Form controls and labels use `font-ui` for readability.
- **Controls:** Inputs and button use consistent rounded corners, subtle borders, and clear focus rings. Primary CTA uses the orange accent (`bg-accent`).

## A11y Spec

- Labels use `htmlFor` and inputs have stable `id`/`name` and `autoComplete` attributes.
- Error is rendered in a region with `role="alert"` and `aria-live="assertive"`.
- When error exists, inputs set `aria-invalid` and `aria-describedby` to reference the error region.
- Focus styles use `focus-visible:ring-*` and `ring-offset-*` for strong keyboard navigation.

## Verification

- Manual: tab through fields and button; verify focus rings and that the error message is announced by screen readers.
- Build: run `npm run build` in `apps/web`.
