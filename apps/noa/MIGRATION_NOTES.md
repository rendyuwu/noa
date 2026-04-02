# NOA Frontend Rewrite Migration Notes

## Visual direction preserved
- Warm, light, airy palette rather than the darker Claude-like styling in `apps/web`.
- Unified navigation for assistant and admin surfaces.
- Card-based content framing with generous spacing and responsive behavior as a first-order requirement.

## Canonical behavior references
- Auth + same-origin proxy behavior: `apps/web/components/lib/auth-store.ts`, `apps/web/components/lib/fetch-helper.ts`, `apps/web/app/api/[...path]/route.ts`
- Assistant route composition: `apps/web/app/(app)/assistant/**`
- Admin route composition and route inventory: `apps/web/app/(admin)/admin/**`
- Admin shell duplication reference to remove: `apps/web/components/admin/admin-sidebar-shell.tsx`
- Legacy/overlapping assistant references to resolve during later phases: `apps/web/components/assistant/**` and `apps/web/components/claude/**`

## Deliberate non-ports in this slice
- No recreation of `components/lib/button.tsx`, `scroll-area.tsx`, or `confirm-dialog.tsx`.
- No copying of Claude-prefixed component names into `apps/noa`.
- No direct browser calls to backend absolute URLs; browser code stays on same-origin `/api/...`.

## Current phase outcome
This slice establishes the scaffold, auth/http boundaries, shared shell contract, and route map placeholders so later assistant/admin parity work can land without recreating the old route-level duplication.
