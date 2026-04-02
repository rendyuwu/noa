"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";

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

function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value) {
    return "-";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }

  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
}

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

export function AuditAdminPage() {
  const [draft, setDraft] = useState<Filters>(DEFAULT_FILTERS);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);

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
      if (seq !== loadSeqRef.current) {
        return;
      }

      setLoading(false);
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
    <section className="space-y-4">
      <div className="rounded-2xl border border-border bg-surface p-4">
        <h2 className="text-lg font-semibold text-text">Audit history</h2>
        <p className="mt-1 text-sm text-muted">Filter and review action requests and receipts.</p>

        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label className="text-sm text-text">
            Tool
            <input
              className="mt-1 w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm"
              value={draft.toolName}
              onChange={(event) => setDraft((current) => ({ ...current, toolName: event.target.value }))}
            />
          </label>
          <label className="text-sm text-text">
            Status
            <input
              className="mt-1 w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm"
              value={draft.status}
              onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))}
            />
          </label>
          <label className="text-sm text-text">
            Terminal phase
            <input
              className="mt-1 w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm"
              value={draft.terminalPhase}
              onChange={(event) => setDraft((current) => ({ ...current, terminalPhase: event.target.value }))}
            />
          </label>
          <label className="text-sm text-text">
            Requested by
            <input
              className="mt-1 w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm"
              value={draft.requestedByEmail}
              onChange={(event) => setDraft((current) => ({ ...current, requestedByEmail: event.target.value }))}
            />
          </label>
          <label className="text-sm text-text">
            From
            <input
              type="date"
              className="mt-1 w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm"
              value={draft.fromDate}
              onChange={(event) => setDraft((current) => ({ ...current, fromDate: event.target.value }))}
            />
          </label>
          <label className="text-sm text-text">
            To
            <input
              type="date"
              className="mt-1 w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm"
              value={draft.toDate}
              onChange={(event) => setDraft((current) => ({ ...current, toDate: event.target.value }))}
            />
          </label>
          <label className="text-sm text-text">
            Thread ID
            <input
              className="mt-1 w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm"
              value={draft.threadId}
              onChange={(event) => setDraft((current) => ({ ...current, threadId: event.target.value }))}
            />
          </label>
          <label className="text-sm text-text">
            Limit
            <input
              type="number"
              min={1}
              max={200}
              className="mt-1 w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm"
              value={draft.limit}
              onChange={(event) => {
                const nextLimit = Number(event.target.value);
                setDraft((current) => ({
                  ...current,
                  limit: Number.isFinite(nextLimit) ? Math.max(1, Math.min(200, nextLimit)) : current.limit,
                }));
              }}
            />
          </label>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="rounded-xl bg-accent px-3 py-2 text-sm font-medium text-accent-foreground"
            onClick={applyFilters}
          >
            Apply filters
          </button>
          <button
            type="button"
            className="rounded-xl border border-border bg-bg px-3 py-2 text-sm font-medium text-text"
            onClick={clearFilters}
          >
            Clear filters
          </button>
          <button
            type="button"
            className="rounded-xl border border-border bg-bg px-3 py-2 text-sm font-medium text-text"
            onClick={() => void loadData()}
          >
            Refresh
          </button>
        </div>
      </div>

      {loadError ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900" role="alert">
          {loadError}
        </div>
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
              <tr>
                <td className="px-3 py-3 text-muted" colSpan={6}>
                  Loading audit events…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td className="px-3 py-3 text-muted" colSpan={6}>
                  No matching action requests.
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={item.actionRequestId}>
                  <td className="px-3 py-3 font-mono text-xs text-muted">{item.actionRequestId}</td>
                  <td className="px-3 py-3 text-text">{item.toolName}</td>
                  <td className="px-3 py-3 text-text">{item.terminalPhase ?? item.status}</td>
                  <td className="px-3 py-3 text-text">{item.risk}</td>
                  <td className="px-3 py-3 text-muted">{formatTimestamp(item.createdAt)}</td>
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
          <button
            type="button"
            className="rounded-xl border border-border bg-bg px-3 py-2 text-sm text-text disabled:opacity-50"
            disabled={cursorStack.length === 0}
            onClick={goPrev}
          >
            Previous
          </button>
          <button
            type="button"
            className="rounded-xl border border-border bg-bg px-3 py-2 text-sm text-text disabled:opacity-50"
            disabled={!nextCursor}
            onClick={goNext}
          >
            Next
          </button>
        </div>
      </div>
    </section>
  );
}
