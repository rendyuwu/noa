'use client'

import { Pagination } from '@gio/bigsu-ui'

// Standalone cursor pagination for the audit surfaces (issue #111). The API is
// cursor-paged, so the total page count is unknown — we render the standard
// BIGSU Pagination control (visually identical to the one DataTable embeds) over
// a rolling window: the current page plus one more whenever the server returned
// a next cursor. Stepping forward one page runs the next-cursor fetch; stepping
// back pops the cursor stack. The DataTable itself stays presentational; this
// control owns the server paging, satisfying the "DataTable presentational,
// standalone Pagination owns server paging" criterion.
export function AuditPagination({
  itemCount,
  pageIndex,
  canGoPrev,
  canGoNext,
  onPrev,
  onNext,
}: {
  itemCount: number
  pageIndex: number
  canGoPrev: boolean
  canGoNext: boolean
  onPrev: () => void
  onNext: () => void
}) {
  // A single page with nothing before or after it needs no pager.
  if (!canGoPrev && !canGoNext) return null

  const pageCount = pageIndex + (canGoNext ? 1 : 0)

  const handlePageChange = (target: number) => {
    if (target < pageIndex && canGoPrev) onPrev()
    else if (target > pageIndex && canGoNext) onNext()
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm text-text-secondary" aria-live="polite">
        {itemCount} result{itemCount === 1 ? '' : 's'} on this page
        {canGoNext ? ' · more available' : ''}
      </p>
      <Pagination page={pageIndex} pageCount={pageCount} onPageChange={handlePageChange} />
    </div>
  )
}
