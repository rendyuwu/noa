"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ComponentProps } from "react";

import { formatTimestampUTC } from "@/components/admin/lib/format-timestamp";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";

type AuditActionRequestListItem = {
  actionRequestId: string;
  threadId: string;
  toolRunId?: string | null;
  receiptId?: string | null;
  toolName: string;
  risk: string;
  status: string;
  requestedByEmail?: string | null;
  decidedAt?: string | null;
  createdAt: string;
  updatedAt: string;
  terminalPhase?: string | null;
  hasReceipt: boolean;
};

type ListAuditActionRequestsResponse = {
  items: AuditActionRequestListItem[];
  nextCursor?: string | null;
};

type Filters = {
  fromDate: string;
  toDate: string;
  toolName: string;
  status: string;
  terminalPhase: string;
  threadId: string;
  requestedByEmail: string;
  limit: number;
};

const DEFAULT_FILTERS: Filters = {
  fromDate: "",
  toDate: "",
  toolName: "",
  status: "",
  terminalPhase: "",
  threadId: "",
  requestedByEmail: "",
  limit: 50,
};

const AUDIT_LOADING_ROW_KEYS = ["audit-loading-row-1", "audit-loading-row-2", "audit-loading-row-3", "audit-loading-row-4", "audit-loading-row-5"];
const AUDIT_LOADING_COLUMN_KEYS = [
  "audit-loading-column-action",
  "audit-loading-column-tool",
  "audit-loading-column-status",
  "audit-loading-column-risk",
  "audit-loading-column-created",
  "audit-loading-column-receipt",
];

function normalizeDateRange(filters: Filters): { from?: string; to?: string } {
  const result: { from?: string; to?: string } = {};

  const fromDate = filters.fromDate.trim();
  if (fromDate) {
    const from = new Date(`${fromDate}T00:00:00.000Z`);
    if (!Number.isNaN(from.getTime())) {
      result.from = from.toISOString();
    }
  }

  const toDate = filters.toDate.trim();
  if (toDate) {
    const to = new Date(`${toDate}T23:59:59.999Z`);
    if (!Number.isNaN(to.getTime())) {
      result.to = to.toISOString();
    }
  }

  return result;
}

type BadgeVariant = NonNullable<ComponentProps<typeof Badge>["variant"]>;

function getStatusBadgeVariant(status: string): BadgeVariant {
  const normalized = status.trim().toLowerCase();

  if (["approved", "completed", "success"].includes(normalized)) {
    return "success";
  }

  if (["denied", "rejected", "failed", "error"].includes(normalized)) {
    return "destructive";
  }

  if (["pending", "reviewing", "in_review"].includes(normalized)) {
    return "warning";
  }

  return "muted";
}

function getRiskBadgeVariant(risk: string): BadgeVariant {
  const normalized = risk.trim().toLowerCase();

  if (normalized === "change") {
    return "warning";
  }

  if (normalized === "read") {
    return "info";
  }

  return "muted";
}

export function AuditAdminPage() {
  const [draft, setDraft] = useState<Filters>(DEFAULT_FILTERS);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false);

  const [items, setItems] = useState<AuditActionRequestListItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSeqRef = useRef(0);

  const queryParams = useMemo(() => {
    const params = new URLSearchParams();
    params.set("limit", String(filters.limit));
    if (cursor) {
      params.set("cursor", cursor);
    }

    if (filters.toolName.trim()) {
      params.set("toolName", filters.toolName.trim());
    }
    if (filters.status.trim()) {
      params.set("status", filters.status.trim());
    }
    if (filters.terminalPhase.trim()) {
      params.set("terminalPhase", filters.terminalPhase.trim());
    }
    if (filters.threadId.trim()) {
      params.set("threadId", filters.threadId.trim());
    }
    if (filters.requestedByEmail.trim()) {
      params.set("requestedByEmail", filters.requestedByEmail.trim());
    }

    const { from, to } = normalizeDateRange(filters);
    if (from) {
      params.set("from", from);
    }
    if (to) {
      params.set("to", to);
    }

    return params;
  }, [cursor, filters]);

  const loadData = useCallback(async () => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setLoadError(null);

    try {
      const qs = queryParams.toString();
      const response = await fetchWithAuth(`/admin/audit/action-requests${qs ? `?${qs}` : ""}`);
      const payload = await jsonOrThrow<ListAuditActionRequestsResponse>(response);

      if (seq !== loadSeqRef.current) {
        return;
      }

      setItems(Array.isArray(payload.items) ? payload.items : []);
      setNextCursor(payload.nextCursor ?? null);
    } catch (error) {
      if (seq !== loadSeqRef.current) {
        return;
      }

      setLoadError(toErrorMessage(error, "Unable to load audit history"));
      setItems([]);
      setNextCursor(null);
    } finally {
      if (seq === loadSeqRef.current) {
        setLoading(false);
      }
    }
  }, [queryParams]);

  useEffect(() => {
    void loadData();

    return () => {
      loadSeqRef.current += 1;
    };
  }, [loadData]);

  const applyFilters = () => {
    setCursor(null);
    setCursorStack([]);
    setFilters(draft);
  };

  const clearFilters = () => {
    setCursor(null);
    setCursorStack([]);
    setDraft(DEFAULT_FILTERS);
    setFilters(DEFAULT_FILTERS);
  };

  const goNext = () => {
    if (!nextCursor) {
      return;
    }

    setCursorStack((current) => [...current, cursor]);
    setCursor(nextCursor);
  };

  const goPrev = () => {
    if (!cursorStack.length) {
      return;
    }

    const priorCursor = cursorStack[cursorStack.length - 1] ?? null;
    setCursorStack((current) => current.slice(0, -1));
    setCursor(priorCursor);
  };

  return (
    <section className="space-y-5">
      <div className="rounded-2xl border border-border bg-surface p-4 shadow-sm">
        <div className="flex flex-col gap-1">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">Audit admin console</p>
          <h2 className="text-lg font-semibold text-text">Audit history</h2>
          <p className="text-sm text-muted">Filter and review action requests and receipts.</p>
        </div>

        <div className="mt-4 grid gap-3 lg:grid-cols-4">
          <div className="text-sm text-text">
            <label htmlFor="audit-tool-name">Tool</label>
            <Input
              id="audit-tool-name"
              className="mt-1 rounded-xl"
              value={draft.toolName}
              onChange={(event) => setDraft((current) => ({ ...current, toolName: event.target.value }))}
            />
          </div>
          <div className="text-sm text-text">
            <label htmlFor="audit-status">Status</label>
            <Input
              id="audit-status"
              className="mt-1 rounded-xl"
              value={draft.status}
              onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))}
            />
          </div>
          <div className="text-sm text-text">
            <label htmlFor="audit-from-date">From</label>
            <Input
              id="audit-from-date"
              type="date"
              className="mt-1 rounded-xl"
              value={draft.fromDate}
              onChange={(event) => setDraft((current) => ({ ...current, fromDate: event.target.value }))}
            />
          </div>
          <div className="text-sm text-text">
            <label htmlFor="audit-to-date">To</label>
            <Input
              id="audit-to-date"
              type="date"
              className="mt-1 rounded-xl"
              value={draft.toDate}
              onChange={(event) => setDraft((current) => ({ ...current, toDate: event.target.value }))}
            />
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" className="rounded-xl" onClick={applyFilters}>
            Apply filters
          </Button>
          <Button type="button" variant="outline" size="sm" className="rounded-xl" onClick={clearFilters}>
            Clear filters
          </Button>
          <Button type="button" variant="outline" size="sm" className="rounded-xl" onClick={() => void loadData()}>
            Refresh
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="rounded-xl"
            aria-expanded={showAdvancedFilters}
            aria-controls="audit-advanced-filters"
            onClick={() => setShowAdvancedFilters((current) => !current)}
          >
            {showAdvancedFilters ? "Hide advanced filters" : "Show advanced filters"}
          </Button>
        </div>

        {showAdvancedFilters ? (
          <div id="audit-advanced-filters" className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <div className="text-sm text-text">
              <label htmlFor="audit-limit">Limit</label>
              <Input
                id="audit-limit"
                type="number"
                min={1}
                max={200}
                className="mt-1 rounded-xl"
                value={draft.limit}
                onChange={(event) => {
                  const nextLimit = Number(event.target.value);
                  setDraft((current) => ({
                    ...current,
                    limit: Number.isFinite(nextLimit) ? Math.max(1, Math.min(200, nextLimit)) : current.limit,
                  }));
                }}
              />
            </div>
            <div className="text-sm text-text">
              <label htmlFor="audit-terminal-phase">Terminal phase</label>
              <Input
                id="audit-terminal-phase"
                className="mt-1 rounded-xl"
                value={draft.terminalPhase}
                onChange={(event) => setDraft((current) => ({ ...current, terminalPhase: event.target.value }))}
              />
            </div>
            <div className="text-sm text-text">
              <label htmlFor="audit-requested-by">Requested by</label>
              <Input
                id="audit-requested-by"
                className="mt-1 rounded-xl"
                value={draft.requestedByEmail}
                onChange={(event) => setDraft((current) => ({ ...current, requestedByEmail: event.target.value }))}
              />
            </div>
            <div className="text-sm text-text md:col-span-2 xl:col-span-4">
              <label htmlFor="audit-thread-id">Thread ID</label>
              <Input
                id="audit-thread-id"
                className="mt-1 rounded-xl"
                value={draft.threadId}
                onChange={(event) => setDraft((current) => ({ ...current, threadId: event.target.value }))}
              />
            </div>
          </div>
        ) : null}
      </div>

      {loadError ? (
        <Alert tone="destructive">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      <div className="overflow-x-auto rounded-2xl border border-border bg-surface">
        <table className="min-w-full divide-y divide-border text-sm">
          <thead>
            <tr className="bg-bg/60 text-left text-xs uppercase tracking-[0.08em] text-muted">
              <th className="px-3 py-2">Action</th>
              <th className="px-3 py-2">Tool</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Risk</th>
              <th className="px-3 py-2">Created</th>
              <th className="px-3 py-2">Receipt</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {loading ? (
              AUDIT_LOADING_ROW_KEYS.map((rowKey) => (
                <tr key={rowKey}>
                  {AUDIT_LOADING_COLUMN_KEYS.map((columnKey) => (
                    <td key={columnKey} className="px-3 py-3">
                      <Skeleton className="h-4 w-20" />
                    </td>
                  ))}
                </tr>
              ))
            ) : items.length === 0 ? (
              <tr>
                <td className="px-3 py-3 text-muted" colSpan={6}>
                  No matching action requests.
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={item.actionRequestId} className="transition-colors hover:bg-surface-2/50">
                  <td className="px-3 py-3 align-top">
                    <div className="space-y-1">
                      <div className="font-mono text-xs font-medium text-text">{item.actionRequestId}</div>
                      <div className="text-xs text-muted">Thread {item.threadId}</div>
                    </div>
                  </td>
                  <td className="px-3 py-3 align-top">
                    <div className="space-y-1">
                      <div className="font-medium text-text">{item.toolName}</div>
                      {item.requestedByEmail ? <div className="text-xs text-muted">{item.requestedByEmail}</div> : null}
                    </div>
                  </td>
                  <td className="px-3 py-3 align-top">
                    <div className="space-y-1">
                      <Badge
                        variant={getStatusBadgeVariant(item.terminalPhase ?? item.status)}
                        className="rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em]"
                      >
                        {item.terminalPhase ?? item.status}
                      </Badge>
                      {item.terminalPhase ? <div className="text-xs text-muted">Status {item.status}</div> : null}
                    </div>
                  </td>
                  <td className="px-3 py-3 align-top">
                    <Badge
                      variant={getRiskBadgeVariant(item.risk)}
                      className="rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em]"
                    >
                      {item.risk}
                    </Badge>
                  </td>
                  <td className="px-3 py-3 align-top text-muted">{formatTimestampUTC(item.createdAt)}</td>
                  <td className="px-3 py-3">
                    {item.hasReceipt ? (
                      <Link
                        href={`/admin/audit/receipts/${item.actionRequestId}`}
                        className="text-accent underline underline-offset-2"
                      >
                        View receipt
                      </Link>
                    ) : (
                      <span className="text-muted">-</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between">
        <div className="text-sm text-muted">Page {cursorStack.length + 1}</div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="rounded-xl"
            disabled={cursorStack.length === 0}
            onClick={goPrev}
          >
            Previous
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="rounded-xl"
            disabled={!nextCursor}
            onClick={goNext}
          >
            Next
          </Button>
        </div>
      </div>
    </section>
  );
}
