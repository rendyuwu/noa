import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

/**
 * Liveness: this process is up (§I.admin-web).
 *
 * It deliberately touches nothing else — not the API, not a cookie, not the DB.
 * A liveness probe that calls a dependency reports the dependency's outage as
 * this app's, and a restart is then the wrong remedy. Readiness (which does read
 * `NOA_API_URL`) is a separate route and separate work (§T.50).
 */
export function GET() {
  return NextResponse.json({ status: 'ok' }, { status: 200 })
}
