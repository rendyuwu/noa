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
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
      <AdminPageHeader title={title} description={description} actions={actions} />

      {filter ? <div className="mt-4">{filter}</div> : null}

      {error && (
        <div role="alert" className="mt-4 rounded-xl border border-destructive/25 bg-destructive/10 px-4 py-3 text-sm text-destructive">
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
            <div key={key} className="animate-pulse rounded-xl border border-border bg-card px-4 py-3">
              <div className="mb-2 h-4 w-1/3 rounded bg-muted" />
              <div className="h-3 w-1/2 rounded bg-muted" />
            </div>
          ))}
        </div>
      )}

      {!loading && empty && (
        <div className="py-12 text-center">
          <p className="text-foreground font-medium">{emptyTitle || "No items"}</p>
          {emptyDescription && (
            <p className="mt-1 text-sm text-muted-foreground">{emptyDescription}</p>
          )}
        </div>
      )}

      {!loading && !empty && (
        <div className="mt-6 space-y-2">{children}</div>
      )}
    </div>
  );
}
