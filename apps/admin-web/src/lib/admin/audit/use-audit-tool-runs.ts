'use client'

import { useCallback, useMemo } from 'react'

import { fetchToolRuns } from './audit-api'
import { activeToolFilterCount, buildToolRunQuery } from './audit-model'
import { useAuditList, type AuditListController } from './use-audit-list'
import { DEFAULT_TOOL_FILTERS, type AuditToolRunListItem, type ToolRunFilters } from './types'

// Tool-run audit list controller (issue #111). Binds the generic server-paged
// list controller to the tool-runs endpoint + query builder, and exposes the
// active-filter count for the FilterBar affordance.
export function useAuditToolRuns(
  enabled: boolean,
): AuditListController<AuditToolRunListItem, ToolRunFilters> & { filterCount: number } {
  const buildQuery = useCallback(
    (filters: ToolRunFilters, cursor: string | null) => buildToolRunQuery(filters, cursor),
    [],
  )
  const controller = useAuditList<AuditToolRunListItem, ToolRunFilters>({
    enabled,
    defaultFilters: DEFAULT_TOOL_FILTERS,
    errorFallback: 'Unable to load tool runs',
    buildQuery,
    fetchPage: fetchToolRuns,
  })
  const filterCount = useMemo(
    () => activeToolFilterCount(controller.activeFilters),
    [controller.activeFilters],
  )
  return { ...controller, filterCount }
}
