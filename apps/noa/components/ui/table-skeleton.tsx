import { Skeleton } from "@/components/ui/skeleton"

type TableSkeletonProps = {
  columns: number
  rows?: number
}

function TableSkeleton({ columns, rows = 5 }: TableSkeletonProps) {
  const rowIds = Array.from({ length: rows }, () => crypto.randomUUID())
  const columnIds = Array.from({ length: columns }, () => crypto.randomUUID())

  return (
    <div className="space-y-3">
      {rowIds.map((rowId) => (
        <div
          key={rowId}
          className="grid gap-3"
          style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        >
          {columnIds.map((columnId) => (
            <Skeleton key={columnId} className="h-8 w-full" />
          ))}
        </div>
      ))}
    </div>
  )
}

export { TableSkeleton }
