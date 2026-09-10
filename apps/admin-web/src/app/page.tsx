import { redirect } from 'next/navigation'

// `/` is not a landing page with somewhere to go: it is the way in, and the
// protected layout is what decides whether the visitor may see anything.
//
// It stays a SERVER redirect. Where it points is now `/home` — the role-aware
// dispatcher — because administration is no longer the whole of this app:
// `/me/tokens` is a surface any verified operator may use, and sending them to
// the first admin vertical answered their first request with a 403. The dispatch
// itself has to be a client decision (it needs the verified session), which is
// exactly why it lives one hop further in rather than here.
export default function RootPage() {
  redirect('/home')
}
