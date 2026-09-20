'use client'

// /admin/audit. The admin gate lives in the section layout; what is left here
// is the route-to-view binding.
//
// The one audit entry point. The ported `/admin/audit/tool-runs` deep link
// existed to preselect one of two tabs; with the action-requests tab gone
// it was a second address for the only view, so it went with it.
export { AuditAdminPage as default } from '@/components/admin/audit/audit-admin-page'
