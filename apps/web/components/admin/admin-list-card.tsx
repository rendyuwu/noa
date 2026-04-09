"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

type AdminListCardProps = {
  title: string;
  subtitle?: string;
  badges?: ReactNode;
  metadata?: ReactNode;
  onClick: () => void;
  selected?: boolean;
  className?: string;
};

export function AdminListCard({
  title,
  subtitle,
  badges,
  metadata,
  onClick,
  selected,
  className,
}: AdminListCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full text-left rounded-xl border border-border bg-card px-4 py-3",
        "hover:bg-accent transition-colors cursor-pointer",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected && "ring-2 ring-primary bg-accent",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground truncate">{title}</span>
            {badges}
          </div>
          {subtitle && (
            <p className="text-sm text-muted-foreground truncate mt-0.5">{subtitle}</p>
          )}
        </div>
        {metadata && (
          <div className="flex items-center gap-2 shrink-0 text-sm text-muted-foreground">
            {metadata}
          </div>
        )}
      </div>
    </button>
  );
}
