'use client'

// /admin/approvals. The admin gate lives in the section layout; what is left
// here is the route-to-view binding.
//
// A page here is lawful because the admin API's contract names the routes behind it. The
// panel once shipped this view against addresses the API did not serve and
// 404'd on every load; it was deleted rather than stubbed, and the rule that
// deletion established is the one this page satisfies rather than sidesteps.
export { ApprovalsAdminPage as default } from '@/components/admin/audit/approvals-admin-page'
