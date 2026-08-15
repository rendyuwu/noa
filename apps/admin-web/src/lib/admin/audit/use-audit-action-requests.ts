'use client'

import { useCallback, useMemo } from 'react'

import { fetchActionRequests } from './audit-api'
import { activeActionFilterCount, buildActionQuery } from './audit-model'
import { useAuditList, type AuditListController } from './use-audit-list'
import { DEFAULT_ACTION_FILTERS, type ActionFilters, type AuditActionRequestListItem } from './types'

// Action-request audit list controller (issue #111). Binds the generic
// server-paged list controller to the action-requests endpoint + query builder,
// and exposes the active-filter count for the FilterBar affordance.
export function useAuditActionRequests(
  enabled: boolean,
): AuditListController<AuditActionRequestListItem, ActionFilters> & { filterCount: number } {
  const buildQuery = useCallback(
    (filters: ActionFilters, cursor: string | null) => buildActionQuery(filters, cursor),
    [],
  )
  const controller = useAuditList<AuditActionRequestListItem, ActionFilters>({
    enabled,
    defaultFilters: DEFAULT_ACTION_FILTERS,
    errorFallback: 'Unable to load audit history',
    buildQuery,
    fetchPage: fetchActionRequests,
  })
  const filterCount = useMemo(
    () => activeActionFilterCount(controller.activeFilters),
    [controller.activeFilters],
  )
  return { ...controller, filterCount }
}
