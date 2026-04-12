"use client";

import type { ReactNode } from "react";

import { AdminPageHeader } from "@/components/admin/admin-page-header";

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
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <AdminPageHeader title={title} description={description} actions={actions} />

      {filter ? (
        <div className="mt-6 rounded-2xl border border-border/80 bg-card/80 px-4 py-4 shadow-sm">
          {filter}
        </div>
      ) : null}

      {error && (
        <div role="alert" className="mt-4 rounded-2xl border border-destructive/25 bg-destructive/10 px-4 py-3 text-sm text-destructive shadow-sm">
          <p>{error}</p>
          {onRetry && (
            <button type="button" onClick={onRetry} className="mt-1 underline">
              Retry
            </button>
          )}
        </div>
      )}

      {loading && (
        <div className="mt-6 space-y-2">
          {Array.from({ length: 5 }, (_, index) => `admin-list-skeleton-${index + 1}`).map((key) => (
            <div key={key} className="animate-pulse rounded-2xl border border-border/80 bg-card/80 px-4 py-3 shadow-sm">
              <div className="mb-2 h-4 w-1/3 rounded bg-muted" />
              <div className="h-3 w-1/2 rounded bg-muted" />
            </div>
          ))}
        </div>
      )}

      {!loading && empty && (
        <div className="mt-6 rounded-2xl border border-border/80 bg-card/80 px-6 py-12 text-center shadow-sm">
          <div className="mx-auto max-w-md">
            <p className="text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">
              Empty state
            </p>
            <p className="mt-2 font-serif text-2xl font-semibold tracking-[-0.02em] text-foreground">
              {emptyTitle || "No items"}
            </p>
            {emptyDescription && <p className="mt-2 text-sm text-muted-foreground">{emptyDescription}</p>}
          </div>
        </div>
      )}

      {!loading && !empty && (
        <div className="mt-6 space-y-3">{children}</div>
      )}
    </div>
  );
}
