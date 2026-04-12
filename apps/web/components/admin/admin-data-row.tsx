import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type AdminDataRowProps = {
  primaryAction: ReactNode;
  statusCell: ReactNode;
  roleCell: ReactNode;
  createdCell: ReactNode;
  lastLoginCell: ReactNode;
  selected?: boolean;
  className?: string;
};

export function AdminDataRow({
  primaryAction,
  statusCell,
  roleCell,
  createdCell,
  lastLoginCell,
  selected = false,
  className,
}: AdminDataRowProps) {
  return (
    <tr
      aria-selected={selected}
      className={cn(
        "border-b border-border/80 transition-colors hover:bg-card/90 last:border-b-0",
        selected ? "bg-accent/35" : "bg-card/80",
        className,
      )}
    >
      <th scope="row" className="px-4 py-3 align-top text-left font-normal">{primaryAction}</th>
      <td className="px-4 py-3 align-top">{statusCell}</td>
      <td className="px-4 py-3 align-top">{roleCell}</td>
      <td className="px-4 py-3 align-top text-sm text-muted-foreground">{createdCell}</td>
      <td className="px-4 py-3 align-top text-sm text-muted-foreground">{lastLoginCell}</td>
    </tr>
  );
}
