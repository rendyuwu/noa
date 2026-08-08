import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

/**
 * Liveness: this process is up (§I.embed).
 *
 * It deliberately touches nothing else — not the API, not a cookie, not the DB.
 * A liveness probe that calls a dependency reports the dependency's outage as
 * this app's, and a restart is then the wrong remedy.
 */
export function GET() {
  return NextResponse.json({ status: 'ok' }, { status: 200 })
}
