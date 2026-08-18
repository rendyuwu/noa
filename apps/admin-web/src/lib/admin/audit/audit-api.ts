import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type { AuditToolRunDetail, ListAuditToolRunsResponse } from './types'

// Transport for the read-only audit vertical (T55). Both calls go through the
// shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the
// session-expiry flow and any non-OK response throws a typed ApiError carrying
// the backend's stable error_code / message / request_id. The audit trail is
// append-only — its writers are the MCP tool path and the approval executor —
// so there are no create/update/delete transports here by design, and the
// service behind these two routes has no way to write at all.
//
// Two transports, not five: the ported action-request list, detail and receipt
// calls named routes NOA has no §I.admin-api row for and answered 404 on every
// load. They are gone rather than stubbed.

export async function fetchToolRuns(query: string): Promise<ListAuditToolRunsResponse> {
  const response = await fetchWithAuth(`/admin/audit/tool-runs${query ? `?${query}` : ''}`)
  const payload = await jsonOrThrow<ListAuditToolRunsResponse>(response)
  return {
    items: Array.isArray(payload.items) ? payload.items : [],
    nextCursor: payload.nextCursor ?? null,
  }
}

export async function fetchToolRunDetail(toolRunId: string): Promise<AuditToolRunDetail> {
  const response = await fetchWithAuth(`/admin/audit/tool-runs/${encodeURIComponent(toolRunId)}`)
  return jsonOrThrow<AuditToolRunDetail>(response)
}
