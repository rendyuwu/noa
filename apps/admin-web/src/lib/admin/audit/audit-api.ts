import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type {
  AuditActionRequestDetail,
  AuditToolRunDetail,
  ListAuditActionRequestsResponse,
  ListAuditToolRunsResponse,
} from './types'

// Transport for the read-only audit vertical (issue #111). Every call goes
// through the shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the
// session-expiry flow and any non-OK response throws a typed ApiError carrying
// the backend's stable detail / error_code / request_id. Audit history is
// append-only: there are no create/update/delete transports here by design.
// Sensitive tool arguments and secret-bearing receipt fields are redacted by
// the API before they reach the wire, so nothing here ever un-redacts.

export async function fetchActionRequests(query: string): Promise<ListAuditActionRequestsResponse> {
  const response = await fetchWithAuth(`/admin/audit/action-requests${query ? `?${query}` : ''}`)
  const payload = await jsonOrThrow<ListAuditActionRequestsResponse>(response)
  return {
    items: Array.isArray(payload.items) ? payload.items : [],
    nextCursor: payload.nextCursor ?? null,
  }
}

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

export async function fetchActionRequestDetail(
  actionRequestId: string,
): Promise<AuditActionRequestDetail> {
  const response = await fetchWithAuth(
    `/admin/audit/action-requests/${encodeURIComponent(actionRequestId)}`,
  )
  return jsonOrThrow<AuditActionRequestDetail>(response)
}

export async function fetchReceiptPayload(
  actionRequestId: string,
): Promise<Record<string, unknown>> {
  const response = await fetchWithAuth(
    `/admin/audit/action-requests/${encodeURIComponent(actionRequestId)}/receipt`,
  )
  return jsonOrThrow<Record<string, unknown>>(response)
}
