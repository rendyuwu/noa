'use client'

import { useCallback, useMemo } from 'react'

import { fetchActionRequests } from './audit-api'
import { activeActionRequestFilterCount, buildActionRequestQuery } from './audit-model'
import { useAuditList, type AuditListController } from './use-audit-list'
import {
  DEFAULT_ACTION_REQUEST_FILTERS,
  type ActionRequestFilters,
  type AuditActionRequestListItem,
} from './types'

// Action-request list controller (§I.admin-api). Binds the same generic
// server-paged controller the tool-run list uses to the authorisation trail's
// endpoint and query builder — the hook's own docstring says it was written for
// two audit lists, and this is the second one arriving four tasks later.
//
// Nothing here is a second copy of the paging, the race guard or the cursor
// stack: those are decisions about a server-paged list rather than about which
// table is behind it, and a private copy would be a second place for the "a slow
// page must not land after a filter change" rule to live.
export function useAuditActionRequests(
  enabled: boolean,
): AuditListController<AuditActionRequestListItem, ActionRequestFilters> & { filterCount: number } {
  const buildQuery = useCallback(
    (filters: ActionRequestFilters, cursor: string | null) =>
      buildActionRequestQuery(filters, cursor),
    [],
  )
  const controller = useAuditList<AuditActionRequestListItem, ActionRequestFilters>({
    enabled,
    defaultFilters: DEFAULT_ACTION_REQUEST_FILTERS,
    errorFallback: 'Unable to load approval requests',
    buildQuery,
    fetchPage: fetchActionRequests,
  })
  const filterCount = useMemo(
    () => activeActionRequestFilterCount(controller.activeFilters),
    [controller.activeFilters],
  )
  return { ...controller, filterCount }
}
