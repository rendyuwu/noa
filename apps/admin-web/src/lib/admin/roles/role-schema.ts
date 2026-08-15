import { z } from 'zod'

// The Zod schema for the create-role form (issue #107). The name is required and
// trimmed here; the backend remains authoritative over name validity and
// uniqueness (INVALID_ROLE_NAME / ROLE_EXISTS / INTERNAL_ROLE_FORBIDDEN, 400/409)
// and its stable detail is surfaced back onto this field on failure.
export const createRoleSchema = z.object({
  name: z.string().trim().min(1, 'Role name is required'),
})

export type CreateRoleValues = z.infer<typeof createRoleSchema>

// The allowlist form is a plain set of opaque tool names; the backend validates
// them (UNKNOWN_TOOLS) and stays authoritative. react-hook-form drives this
// through zodResolver, same as the create form.
export const roleToolsSchema = z.object({
  tools: z.array(z.string()),
})

export type RoleToolsValues = z.infer<typeof roleToolsSchema>
