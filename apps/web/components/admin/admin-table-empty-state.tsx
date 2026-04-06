import { TableIcon as DatabaseIcon } from "@radix-ui/react-icons";

import { Skeleton } from "@/components/ui/skeleton";

export function AdminTableLoadingRows({ columns, rows = 5 }: { columns: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, index) => (
        <tr key={index} className="border-t border-border/60">
          <td colSpan={columns} className="px-4 py-3">
            <Skeleton className="h-10 w-full rounded-lg" />
          </td>
        </tr>
      ))}
    </>
  );
}

export function AdminTableEmptyState({
  columns,
  title,
  description,
}: {
  columns: number;
  title: string;
  description: string;
}) {
  return (
    <tr className="border-t border-border/60">
      <td colSpan={columns} className="px-4 py-10">
        <div className="flex flex-col items-center justify-center gap-3 text-center">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <DatabaseIcon width={18} height={18} />
          </div>
          <div>
            <p className="text-sm font-medium text-foreground">{title}</p>
            <p className="mt-1 text-sm text-muted-foreground">{description}</p>
          </div>
        </div>
      </td>
    </tr>
  );
}
