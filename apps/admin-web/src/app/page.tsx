import { redirect } from 'next/navigation'

// This app is the admin panel and nothing else (§I.admin-web) — the chat surface
// it once shared a root with lives outside NOA now. So `/` is not a landing page
// with somewhere to go: it is the way to the first admin vertical, and the
// protected layout is what decides whether the visitor may see it.
export default function RootPage() {
  redirect('/admin/users')
}
