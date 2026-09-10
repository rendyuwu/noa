import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type {
  AuditActionReceipt,
  AuditActionRequestDetail,
  AuditToolRunDetail,
  ListAuditActionRequestsResponse,
  ListAuditToolRunsResponse,
} from './types'

// Transport for the read-only audit vertical. Both calls go through the
// shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the
// session-expiry flow and any non-OK response throws a typed ApiError carrying
// the backend's stable error_code / message / request_id. The audit trail is
// append-only — its writers are the MCP tool path and the approval executor —
// so there are no create/update/delete transports here by design, and the
// service behind these two routes has no way to write at all.
//
// Five transports. Two read `tool_runs`; three read the CHANGE authorisation
// trail — `/admin/action-requests`, its detail and its receipt — which are the
// addresses the ported panel once called before they existed. They answered 404
// on every load then and were deleted rather than stubbed; they are back because
// §I.admin-api now names them, which is the only thing that ever made them
// legitimate. Every one is a GET: the trail's writers are the approval gate, the
// decision path and the executor, and the service behind these routes has no way
// to write at all.

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


export async function fetchActionRequests(
  query: string,
): Promise<ListAuditActionRequestsResponse> {
  const response = await fetchWithAuth(`/admin/action-requests${query ? `?${query}` : ''}`)
  const payload = await jsonOrThrow<ListAuditActionRequestsResponse>(response)
  return {
    items: Array.isArray(payload.items) ? payload.items : [],
    nextCursor: payload.nextCursor ?? null,
  }
}

export async function fetchActionRequestDetail(
  actionRequestId: string,
): Promise<AuditActionRequestDetail> {
  const response = await fetchWithAuth(
    `/admin/action-requests/${encodeURIComponent(actionRequestId)}`,
  )
  return jsonOrThrow<AuditActionRequestDetail>(response)
}

// Answers 404 `action_receipt_not_found` for every denied, expired and still
// pending request, which is a fact rather than a fault — the caller renders it
// as "no run was started" and never as a dead link. That is why it is a separate
// transport from the detail above: folding it in would make the detail fail for
// the majority of rows.
export async function fetchActionReceipt(actionRequestId: string): Promise<AuditActionReceipt> {
  const response = await fetchWithAuth(
    `/admin/action-requests/${encodeURIComponent(actionRequestId)}/receipt`,
  )
  return jsonOrThrow<AuditActionReceipt>(response)
}
