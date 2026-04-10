import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

type AdminStatusBadgeTone = "muted" | "success" | "danger" | "warning" | "accent" | "outline";

type AdminStatusBadgeProps = ComponentProps<"span"> & {
  tone?: AdminStatusBadgeTone;
  children: ReactNode;
};

const toneClasses: Record<AdminStatusBadgeTone, string> = {
  muted: "status-badge-muted",
  success: "status-badge-success",
  danger: "status-badge-danger",
  warning: "status-badge-warning",
  accent: "status-badge-accent",
  outline: "status-badge-outline",
};

export function AdminStatusBadge({ tone = "muted", children, className, ...props }: AdminStatusBadgeProps) {
  return (
    <span {...props} className={cn("status-badge", toneClasses[tone], className)}>
      {children}
    </span>
  );
}
