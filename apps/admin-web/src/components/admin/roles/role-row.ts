// The row shape the Roles DataTable renders (issue #107). The controller keeps
// roles as a name list plus a separate count map; the page projects them into
// this flat row so the table has a stable per-row identity (`name`) and the
// count column can render its async state (undefined → dash).
export type RoleRow = {
  name: string
  toolCount: number | undefined
}
