"use client";

import type { ReactNode } from "react";

type AdminListLayoutProps = {
  title: string;
  description?: string;
  actions?: ReactNode;
  filter?: ReactNode;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  empty?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  children: ReactNode;
};

export function AdminListLayout({
  title,
  description,
  actions,
  filter,
  loading,
  error,
  onRetry,
  empty,
  emptyTitle,
  emptyDescription,
  children,
}: AdminListLayoutProps) {
  return (
    <div className="max-w-4xl mx-auto px-4 py-6 sm:px-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">{title}</h1>
          {description && (
            <p className="text-sm text-muted-foreground mt-1">{description}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </div>

      {/* Filter bar */}
      {filter && <div className="mb-4">{filter}</div>}

      {/* Error */}
      {error && (
        <div role="alert" className="rounded-xl border border-destructive/25 bg-destructive/10 px-4 py-3 text-sm text-destructive mb-4">
          <p>{error}</p>
          {onRetry && (
            <button type="button" onClick={onRetry} className="underline mt-1">
              Retry
            </button>
          )}
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="rounded-xl border border-border bg-card px-4 py-3 animate-pulse">
              <div className="h-4 bg-muted rounded w-1/3 mb-2" />
              <div className="h-3 bg-muted rounded w-1/2" />
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && empty && (
        <div className="text-center py-12">
          <p className="text-foreground font-medium">{emptyTitle || "No items"}</p>
          {emptyDescription && (
            <p className="text-sm text-muted-foreground mt-1">{emptyDescription}</p>
          )}
        </div>
      )}

      {/* List content */}
      {!loading && !empty && (
        <div className="space-y-2">{children}</div>
      )}
    </div>
  );
}
