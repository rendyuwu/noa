// Shared admin data coercion. The Users and Roles verticals normalise
// loosely-typed API payloads the same way: keep only string members, and accept
// a role list as either bare strings or `{ name }` objects. Extracted here once
// both verticals proved they need the identical behavior — this encodes
// contract-shape handling, not app-specific presentation.
//
// `coerceString`/`coerceRecord` arrived with the receipt parser (§T.48), which
// had its own copies in the old repo's chat lib. One home for the coercers keeps
// the audit receipt and the server verticals agreeing on what counts as a string
// and what counts as a record.

// Narrow an unknown to a string, or to undefined when it is anything else.
export function coerceString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

// Narrow an unknown to a plain object. Arrays are excluded deliberately: they
// are objects to `typeof`, and a caller reaching for a named field on one gets
// `undefined` rather than the shape mismatch it should have seen.
export function coerceRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

// Accept only string members from an unknown array.
export function coerceStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

// Roles arrive either as bare strings or as `{ name }` objects; normalise both to
// a plain string[] without ever reaching for `any`.
export function coerceRoleNames(value: unknown): string[] {
  if (!Array.isArray(value)) return []

  const strings = value.filter((v): v is string => typeof v === 'string')
  if (strings.length) return strings

  const names: string[] = []
  for (const item of value) {
    if (item && typeof item === 'object' && 'name' in item) {
      const name = (item as { name: unknown }).name
      if (typeof name === 'string') names.push(name)
    }
  }
  return names
}
