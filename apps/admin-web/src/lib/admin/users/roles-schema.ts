import { z } from 'zod'

// The single Zod schema for the role-assignment form (issue #106). Roles are
// opaque names here; the backend validates existence and internal-role rules
// (INVALID_ROLE_NAME / UNKNOWN_ROLES / INTERNAL_ROLE_FORBIDDEN, 400) and stays
// authoritative. react-hook-form drives this through zodResolver.
export const roleAssignmentSchema = z.object({
  roles: z.array(z.string()),
})

export type RoleAssignmentValues = z.infer<typeof roleAssignmentSchema>
