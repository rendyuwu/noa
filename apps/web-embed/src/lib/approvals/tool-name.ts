/**
 * The tool name an operator reads, from the tool name the API sends.
 *
 * Its own file rather than a block inside the card: the card renders the label in its heading and
 * the copied summary block carries the same label beside the raw name, so one definition serves
 * both and neither grows a private copy of the spelling rules.
 */

const KNOWN_TOOL_PREFIXES = ['proxmox_', 'whm_', 'pmg_']
const TOOL_WORD_OVERRIDES: Record<string, string> = {
  csf: 'CSF',
  whm: 'WHM',
  rbac: 'RBAC',
  ldap: 'LDAP',
  vm: 'VM',
  pmg: 'PMG',
}

/**
 * A raw tool name as a readable label. The raw name stays on the card in the heading's `title`.
 *
 * **Duplicated from `apps/admin-web/src/lib/admin/audit/audit-format.ts`, deliberately, and it
 * stays duplicated.** The two web apps are independent packages with their own lockfiles, their
 * own CI and their own deploy; this one's eslint refuses any `apps/admin-web` import outright, and
 * the `core/` the two really do share is Python. Sharing these fifteen lines would cost either a
 * third npm package or the cross-app import the fence exists to forbid, and both are more than
 * the duplication is worth. Kept byte-identical to the other copy so a reader diffing the two can
 * see at a glance that they have not drifted.
 */
export function humanizeToolName(value: string): string {
  const raw = value.trim()
  const withoutPrefix = KNOWN_TOOL_PREFIXES.reduce(
    (current, prefix) => (current.startsWith(prefix) ? current.slice(prefix.length) : current),
    raw,
  )
  const words = withoutPrefix
    .split(/[_-]+/)
    .filter(Boolean)
    .map((word) => {
      const lower = word.toLowerCase()
      return TOOL_WORD_OVERRIDES[lower] ?? lower.charAt(0).toUpperCase() + lower.slice(1)
    })
  return words.length === 0 ? raw : words.join(' ')
}
