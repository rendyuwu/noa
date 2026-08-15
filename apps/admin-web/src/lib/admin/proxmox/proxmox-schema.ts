import { z } from 'zod'

import { validateProxmoxServerForm, type ProxmoxServerFormState } from './proxmox-form'

// Zod schema factory for the Proxmox server dialog (issue #109). BIGSU mandates
// react-hook-form + zodResolver, but the server form's rules are mode-dependent
// (write-only secret required on create only). Rather than duplicate that logic,
// the schema delegates to the single validateProxmoxServerForm source of truth
// and routes its first failure to the exact field, so FormField renders per-field
// error text. The backend request models stay authoritative for charset/HTTPS
// normalization/uniqueness.
export function buildProxmoxServerFormSchema(mode: 'create' | 'update') {
  return z.custom<ProxmoxServerFormState>().superRefine((form, ctx) => {
    const error = validateProxmoxServerForm(form, mode)
    if (error) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: error.message, path: [error.field] })
    }
  })
}
