"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ArrowLeftIcon,
  ArrowRightIcon,
  FileTextIcon,
  IdCardIcon,
  MixerHorizontalIcon,
  MinusCircledIcon,
} from "@radix-ui/react-icons";

import { AdminPageHeader } from "@/components/admin/admin-page-header";
import { AdminStatusBadge } from "@/components/admin/admin-status-badge";
import { AdminTableEmptyState, AdminTableLoadingRows } from "@/components/admin/admin-table-empty-state";
import { DisclosureSection } from "@/components/assistant/inline-disclosure";
import { Button } from "@/components/ui/button";
import { toUserMessage } from "@/components/lib/error-message";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

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

function parseIsoDate(value: unknown): Date | null {
  if (typeof value !== "string" || !value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date;
}

function formatRelativeTime(date: Date): string {
  const diffMs = Date.now() - date.getTime();
  if (!Number.isFinite(diffMs) || diffMs < 0) return "";

  const diffSeconds = Math.floor(diffMs / 1000);
  if (diffSeconds < 60) return "just now";

  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes}m ago`;

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 30) return `${diffDays}d ago`;

  return "";
}

function formatCreated(value: unknown): { primary: string; secondary: string; title: string } {
  const date = parseIsoDate(value);
  if (!date) {
    return {
      primary: "-",
      secondary: "",
      title: "",
    };
  }

  const primary = new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    year: "numeric",
  }).format(date);

  const secondary = new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(date);

  const relative = formatRelativeTime(date);
  const secondaryWithRelative = relative ? `${secondary} · ${relative}` : secondary;

  return {
    primary,
    secondary: secondaryWithRelative,
    title: date.toISOString(),
  };
}

function humanizeToolName(value: string): string {
  const raw = value.trim();
  const withoutPrefix = raw.startsWith("whm_") ? raw.slice("whm_".length) : raw;
  const words = withoutPrefix
    .split(/[_\-]+/)
    .filter(Boolean)
    .map((word) => {
      const lower = word.toLowerCase();
      if (lower === "csf") return "CSF";
      if (lower === "whm") return "WHM";
      if (lower === "rbac") return "RBAC";
      if (lower === "ldap") return "LDAP";
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    });

  if (words.length === 0) return raw;
  return words.join(" ");
}

function formatRisk(value: string): { label: string; title: string } {
  const normalized = value.trim().toUpperCase();
  if (normalized === "CHANGE") {
    return { label: "Change", title: "Mutating tool" };
  }
  if (normalized === "READ") {
    return { label: "Read", title: "Read-only tool" };
  }
  return { label: normalized || "Unknown", title: normalized ? `Tool risk: ${normalized}` : "" };
}

function compactId(value: string, { head = 4, tail = 4 }: { head?: number; tail?: number } = {}): string {
  const normalized = value.trim();
  if (!normalized) return "-";
  if (normalized.length <= head + tail + 3) return normalized;
  return `${normalized.slice(0, head)}...${normalized.slice(-tail)}`;
}

function IdRow({ label, value }: { label: string; value?: string | null }) {
  const [expanded, setExpanded] = useState(false);

  const normalized = value?.trim() ?? "";
  if (!normalized) {
    return (
      <div className="grid items-start gap-2 sm:grid-cols-[7rem_minmax(0,1fr)]">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{label}</div>
        <div className="font-mono text-[11px] text-muted-foreground">-</div>
      </div>
    );
  }

  const shown = expanded ? normalized : compactId(normalized);

  return (
    <div className="grid items-start gap-2 sm:grid-cols-[7rem_minmax(0,1fr)]">
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{label}</div>
      <button
        type="button"
        title={normalized}
        aria-label={`${expanded ? "Hide" : "Show"} full ${label}`}
        onClick={() => setExpanded((prev) => !prev)}
        className="min-w-0 text-left font-mono text-[11px] text-foreground transition-colors hover:text-foreground/90"
      >
        <span className="break-all">{shown}</span>
      </button>
    </div>
  );
}

function statusLabel(item: AuditActionRequestListItem): {
  label: string;
  tone: "muted" | "success" | "danger" | "accent";
} {
  const phase = (item.terminalPhase ?? "").toLowerCase();
  if (phase === "completed") return { label: "Finished", tone: "success" };
  if (phase === "failed") return { label: "Failed", tone: "danger" };
  if (phase === "denied") return { label: "Denied", tone: "danger" };

  const status = (item.status ?? "").toUpperCase();
  if (status === "PENDING") return { label: "Pending", tone: "muted" };
  if (status === "APPROVED") return { label: "Approved", tone: "accent" };
  if (status === "DENIED") return { label: "Denied", tone: "danger" };
  return { label: status || "Unknown", tone: "muted" };
}

function normalizeDateRange(filters: Filters): { from?: string; to?: string } {
  const fromDate = filters.fromDate.trim();
  const toDate = filters.toDate.trim();
  const result: { from?: string; to?: string } = {};

  if (fromDate) {
    const from = new Date(`${fromDate}T00:00:00.000Z`);
    if (!Number.isNaN(from.getTime())) {
      result.from = from.toISOString();
    }
  }

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

  const [openIdsForActionRequestId, setOpenIdsForActionRequestId] = useState<string | null>(null);

  const loadSeqRef = useRef(0);

  const queryParams = useMemo(() => {
    const params = new URLSearchParams();
    params.set("limit", String(filters.limit));
    if (cursor) params.set("cursor", cursor);
    if (filters.toolName.trim()) params.set("toolName", filters.toolName.trim());
    if (filters.status.trim()) params.set("status", filters.status.trim());
    if (filters.terminalPhase.trim()) params.set("terminalPhase", filters.terminalPhase.trim());
    if (filters.threadId.trim()) params.set("threadId", filters.threadId.trim());
    if (filters.requestedByEmail.trim()) {
      params.set("requestedByEmail", filters.requestedByEmail.trim());
    }

    const { from, to } = normalizeDateRange(filters);
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    return params;
  }, [cursor, filters]);

  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (filters.fromDate.trim()) count += 1;
    if (filters.toDate.trim()) count += 1;
    if (filters.toolName.trim()) count += 1;
    if (filters.status.trim()) count += 1;
    if (filters.terminalPhase.trim()) count += 1;
    if (filters.threadId.trim()) count += 1;
    if (filters.requestedByEmail.trim()) count += 1;
    return count;
  }, [filters]);

  const pageIndex = cursorStack.length + 1;

  const loadData = useCallback(async () => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setLoadError(null);

    try {
      const qs = queryParams.toString();
      const response = await fetchWithAuth(`/admin/audit/action-requests${qs ? `?${qs}` : ""}`);
      const payload = await jsonOrThrow<ListAuditActionRequestsResponse>(response);
      if (seq !== loadSeqRef.current) return;
      setItems(Array.isArray(payload.items) ? payload.items : []);
      setNextCursor(payload.nextCursor ?? null);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      setLoadError(toUserMessage(error, "Unable to load audit history"));
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
    if (!nextCursor) return;
    setCursorStack((prev) => [...prev, cursor]);
    setCursor(nextCursor);
  };

  const goPrev = () => {
    if (cursorStack.length === 0) return;
    const priorCursor = cursorStack[cursorStack.length - 1] ?? null;
    setCursorStack((prev) => prev.slice(0, -1));
    setCursor(priorCursor);
  };

  const toggleIds = (actionRequestId: string) => {
    setOpenIdsForActionRequestId((prev) => (prev === actionRequestId ? null : actionRequestId));
  };

  return (
    <main className="min-h-dvh bg-background px-4 py-8 sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6">
        <AdminPageHeader
          title="Audit"
          description="Review approvals, executions, and receipts across all threads."
          actions={loading ? <div className="font-sans text-sm text-muted-foreground">Loading...</div> : undefined}
        />

        <div className="editorial-subpanel mt-6 p-4">
          <DisclosureSection
            title="Filters"
            icon={<MixerHorizontalIcon width={16} height={16} />}
            meta={activeFilterCount > 0 ? `${activeFilterCount} active` : undefined}
            defaultOpen={activeFilterCount > 0}
          >
            <div className="grid gap-3 font-sans md:grid-cols-2 lg:grid-cols-4">
            <label className="text-xs text-muted-foreground">
              From
              <input
                type="date"
                value={draft.fromDate}
                onChange={(e) => setDraft((prev) => ({ ...prev, fromDate: e.target.value }))}
                className="input mt-1"
              />
            </label>
            <label className="text-xs text-muted-foreground">
              To
              <input
                type="date"
                value={draft.toDate}
                onChange={(e) => setDraft((prev) => ({ ...prev, toDate: e.target.value }))}
                className="input mt-1"
              />
            </label>
            <label className="text-xs text-muted-foreground">
              Tool
              <input
                value={draft.toolName}
                onChange={(e) => setDraft((prev) => ({ ...prev, toolName: e.target.value }))}
                placeholder='e.g. "whm_create_account"'
                className="input mt-1"
              />
            </label>
            <label className="text-xs text-muted-foreground">
              Requested By
              <input
                value={draft.requestedByEmail}
                onChange={(e) => setDraft((prev) => ({ ...prev, requestedByEmail: e.target.value }))}
                placeholder="email contains..."
                className="input mt-1"
              />
            </label>
            <label className="text-xs text-muted-foreground">
              Status
              <select
                value={draft.status}
                onChange={(e) => setDraft((prev) => ({ ...prev, status: e.target.value }))}
                className="input mt-1"
              >
                <option value="">Any</option>
                <option value="PENDING">PENDING</option>
                <option value="APPROVED">APPROVED</option>
                <option value="DENIED">DENIED</option>
              </select>
            </label>
            <label className="text-xs text-muted-foreground">
              Terminal Phase
              <select
                value={draft.terminalPhase}
                onChange={(e) => setDraft((prev) => ({ ...prev, terminalPhase: e.target.value }))}
                className="input mt-1"
              >
                <option value="">Any</option>
                <option value="completed">completed</option>
                <option value="failed">failed</option>
                <option value="denied">denied</option>
              </select>
            </label>
            <label className="text-xs text-muted-foreground">
              Thread ID
              <input
                value={draft.threadId}
                onChange={(e) => setDraft((prev) => ({ ...prev, threadId: e.target.value }))}
                placeholder="uuid"
                className="input mt-1 font-mono text-[12px]"
              />
            </label>
            <label className="text-xs text-muted-foreground">
              Page Size
              <select
                value={String(draft.limit)}
                onChange={(e) =>
                  setDraft((prev) => ({
                    ...prev,
                    limit: Number.parseInt(e.target.value, 10) || DEFAULT_FILTERS.limit,
                  }))
                }
                className="input mt-1"
              >
                <option value="25">25</option>
                <option value="50">50</option>
                <option value="100">100</option>
                <option value="200">200</option>
              </select>
            </label>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={applyFilters} disabled={loading}>
              Apply
            </Button>
            <Button size="sm" variant="outline" onClick={clearFilters} disabled={loading}>
              Clear
            </Button>
          </div>
          </DisclosureSection>
        </div>

        {loadError ? (
          <div role="alert" aria-live="assertive" className="danger-zone mt-4 font-sans">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="danger-zone-label text-xs font-semibold uppercase tracking-[0.12em]">
                  Unable to load audit history
                </div>
                <div className="danger-zone-copy mt-1 text-sm">{loadError}</div>
              </div>
              <Button className="shrink-0" disabled={loading} onClick={() => void loadData()} size="sm">
                Retry
              </Button>
            </div>
          </div>
        ) : null}

        <div className="editorial-subpanel mt-6 overflow-hidden p-0">
          <table className="w-full font-sans text-sm">
            <thead className="bg-accent text-accent-foreground">
              <tr>
                <th className="w-[13rem] px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Created</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Event</th>
                <th className="w-[16rem] px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Context</th>
                <th className="w-[10rem] px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.length === 0 ? (
                loading ? (
                  <AdminTableLoadingRows columns={4} />
                ) : loadError ? (
                  <tr>
                    <td className="px-4 py-6 text-sm text-muted-foreground" colSpan={4}>
                      Unable to load audit history.
                    </td>
                  </tr>
                ) : activeFilterCount ? (
                  <tr>
                    <td className="px-4 py-6 text-sm text-muted-foreground" colSpan={4}>
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">No results match your filters.</div>
                        <Button size="sm" variant="outline" onClick={clearFilters}>
                          Clear filters
                        </Button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <AdminTableEmptyState
                    columns={4}
                    title="No audit events"
                    description="Approval receipts and audit activity will appear here."
                  />
                )
              ) : (
                items.map((item) => {
                  const created = formatCreated(item.createdAt);
                  const requestedBy = item.requestedByEmail?.trim() || "Unknown";
                  const displayName = humanizeToolName(item.toolName);
                  const risk = formatRisk(item.risk);
                  const label = statusLabel(item);
                  const idsOpen = openIdsForActionRequestId === item.actionRequestId;

                  const actionChipClass =
                    "inline-flex items-center gap-1 rounded-full border border-border bg-background/20 px-2.5 py-1 text-xs font-medium text-primary transition hover:bg-accent hover:text-foreground";
                  const noReceiptClass =
                    "inline-flex items-center gap-1 rounded-full border border-border/60 border-dashed bg-transparent px-2.5 py-1 text-xs font-medium text-muted-foreground opacity-70";

                  return (
                    <Fragment key={item.actionRequestId}>
                      <tr className="transition-colors hover:bg-accent/40">
                        <td className="px-4 py-3 whitespace-nowrap text-muted-foreground" title={created.title}>
                          <div className="text-sm text-foreground/90">{created.primary}</div>
                          <div className="mt-0.5 font-sans text-xs text-muted-foreground">{created.secondary}</div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="min-w-0">
                            <div className="truncate font-medium text-foreground" title={item.toolName}>
                              {displayName}
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-sans text-xs text-muted-foreground">
                              <span>
                                by <span className="font-medium text-foreground/90">{requestedBy}</span>
                              </span>
                              <span className="text-border/70">•</span>
                              <AdminStatusBadge tone="outline" className="font-medium" title={risk.title}>
                                {risk.label}
                              </AdminStatusBadge>
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap items-center gap-2">
                            {item.hasReceipt ? (
                              <Link
                                href={`/admin/audit/receipts/${encodeURIComponent(item.actionRequestId)}`}
                                className={actionChipClass}
                              >
                                <FileTextIcon width={14} height={14} />
                                Receipt
                              </Link>
                            ) : (
                              <span className={noReceiptClass} aria-disabled="true">
                                <MinusCircledIcon width={14} height={14} />
                                No receipt
                              </span>
                            )}
                            <button
                              type="button"
                              aria-expanded={idsOpen}
                              aria-controls={`audit-ids-${item.actionRequestId}`}
                              onClick={() => toggleIds(item.actionRequestId)}
                              className={actionChipClass}
                            >
                              <IdCardIcon width={14} height={14} />
                              {idsOpen ? "Hide IDs" : "IDs"}
                            </button>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right align-top">
                          <AdminStatusBadge tone={label.tone}>{label.label}</AdminStatusBadge>
                        </td>
                      </tr>
                      {idsOpen ? (
                        <tr className="bg-background/10">
                          <td colSpan={4} className="px-4 py-3">
                            <div
                              id={`audit-ids-${item.actionRequestId}`}
                              className="editorial-subpanel px-4 py-3"
                            >
                              <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                                Identifiers
                              </div>
                              <div className="mt-3 grid gap-3">
                                <IdRow label="Thread" value={item.threadId} />
                                <IdRow label="Action" value={item.actionRequestId} />
                                <IdRow label="Tool run" value={item.toolRunId} />
                                <IdRow label="Receipt" value={item.receiptId} />
                              </div>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>

          {!loading && !loadError && (items.length > 0 || cursorStack.length > 0 || Boolean(nextCursor)) ? (
            <div className="border-t border-border/70 bg-card/60 px-4 py-3 font-sans">
              <div className="grid gap-2 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
                <div className="text-sm text-muted-foreground">
                  Showing {items.length} result{items.length === 1 ? "" : "s"} on this page.
                </div>
                <div className="flex items-center justify-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1"
                    onClick={goPrev}
                    disabled={cursorStack.length === 0}
                  >
                    <ArrowLeftIcon width={16} height={16} />
                    Prev
                  </Button>
                  <div className="font-sans text-xs text-muted-foreground">Page {pageIndex}</div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1"
                    onClick={goNext}
                    disabled={!nextCursor}
                  >
                    Next
                    <ArrowRightIcon width={16} height={16} />
                  </Button>
                </div>
                <div className="text-sm text-muted-foreground sm:text-right">
                  {nextCursor ? "More results available." : "End of results."}
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </main>
  );
}
