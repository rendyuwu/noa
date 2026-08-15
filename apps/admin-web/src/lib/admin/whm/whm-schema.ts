import { z } from 'zod'

import type { WhmServer } from './types'
import { validateWhmServerForm, type WhmServerFormState } from './whm-form'

// Zod schema factory for the WHM server dialog (issue #108). BIGSU mandates
// react-hook-form + zodResolver, but the server form's rules are cross-field and
// mode/existing-server dependent (write-only secrets, SSH auth-mode branches).
// Rather than duplicate that logic, the schema delegates to the single
// validateWhmServerForm source of truth and routes its first failure to the
// exact field, so FormField renders per-field error text. The backend request
// models stay authoritative for charset/range/uniqueness.
export function buildWhmServerFormSchema(
  mode: 'create' | 'update',
  existingServer: WhmServer | null,
) {
  return z
    .custom<WhmServerFormState>()
    .superRefine((form, ctx) => {
      const error = validateWhmServerForm(form, mode, existingServer)
      if (error) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, message: error.message, path: [error.field] })
      }
    })
}

// Zod schema for the reseller-token dialog (issue #108). The token itself is
// write-only and NOT part of the validated shape here (it is optional on rotate,
// required on create) — it is validated in the submit handler and never stored.
// Owner and API username charset/uniqueness stay authoritative on the backend
// (WHM_SERVER_TOKEN_OWNER_EXISTS, "valid WHM username", 400/409). react-hook-form
// drives this through zodResolver, same as the Users/Roles verticals.
export const resellerTokenSchema = z.object({
  ownerUsername: z.string().trim().min(1, 'Owner username is required'),
  apiUsername: z.string().trim().min(1, 'API username is required'),
})

export type ResellerTokenValues = z.infer<typeof resellerTokenSchema>
