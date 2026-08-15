import { z } from 'zod'

import type { PmgServer } from './types'
import { validatePmgServerForm, type PmgServerFormState } from './pmg-form'

// Zod schema factory for the PMG server dialog (issue #110). BIGSU mandates
// react-hook-form + zodResolver, but the server form's rules are cross-field and
// mode/existing-server dependent (write-only secrets, SSH auth-mode branches).
// Rather than duplicate that logic, the schema delegates to the single
// validatePmgServerForm source of truth and routes its first failure to the
// exact field, so FormField renders per-field error text. The backend request
// models stay authoritative for charset/range/uniqueness.
export function buildPmgServerFormSchema(mode: 'create' | 'update', existingServer: PmgServer | null) {
  return z.custom<PmgServerFormState>().superRefine((form, ctx) => {
    const error = validatePmgServerForm(form, mode, existingServer)
    if (error) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: error.message, path: [error.field] })
    }
  })
}
