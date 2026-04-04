import { Skeleton } from "@/components/ui/skeleton"

type TableSkeletonProps = {
  columns: number
  rows?: number
}

function TableSkeleton({ columns, rows = 5 }: TableSkeletonProps) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div
          key={rowIndex}
          className="grid gap-3"
          style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        >
          {Array.from({ length: columns }).map((_, columnIndex) => (
            <Skeleton key={columnIndex} className="h-8 w-full" />
          ))}
        </div>
      ))}
    </div>
  )
}

export { TableSkeleton }
