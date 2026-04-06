"use client";

import { useEffect, useId, useMemo, useState } from "react";

import {
  makeAssistantToolUI,
  useAssistantState,
  useAssistantTransportSendCommand,
} from "@assistant-ui/react";
import { ChevronRightIcon } from "@radix-ui/react-icons";
import type { ReactNode } from "react";

import { DetailSections } from "@/components/assistant/detail-sections";
import { getApprovalLifecyclePresentation } from "@/components/assistant/approval-lifecycle-ui";
import {
  coerceDetailEvidenceSections,
  extractLatestCanonicalActionRequests,
} from "@/components/assistant/approval-state";

function coerceString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function prettifyToolName(value: string): string {
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

type DecisionState = "approving" | "denying";

function summarizeDetails(items: { label: string; value: string }[]): string | null {
  const preview = items
    .slice(0, 3)
    .map((item) => `${item.label}: ${item.value}`)
    .join(" · ");
  return preview || null;
}

function getReceiptLabel(lifecycleStatus: string): string {
  switch (lifecycleStatus) {
    case "finished":
      return "Done";
    case "failed":
      return "Failed";
    case "denied":
      return "Denied";
    case "approved":
      return "Approved";
    case "executing":
      return "Running";
    default:
      return "Needs approval";
  }
}

function Actions({ args }: { args: Record<string, unknown> }) {
  const sendCommand = useAssistantTransportSendCommand();
  const actionRequestId = coerceString(args.actionRequestId) ?? "";
  const toolName = coerceString(args.toolName) ?? "unknown";
  const activity = coerceString(args.activity) ?? `Run ${prettifyToolName(toolName).toLowerCase()}`;
  const evidenceSections = coerceDetailEvidenceSections(args.evidenceSections) ?? [];
  const beforeState = coerceDetailEvidenceSections([{ title: "Before state", items: args.beforeState }]) ?? [];
  const argumentSummary =
    coerceDetailEvidenceSections([{ title: "Requested change", items: args.argumentSummary }]) ?? [];
  const threadMessages = useAssistantState(({ thread }: any) => thread?.messages);
  const lifecycleStatus =
    extractLatestCanonicalActionRequests(threadMessages)?.find(
      (request) => request.actionRequestId === actionRequestId,
    )?.lifecycleStatus ?? "requested";
  const copy = getApprovalLifecyclePresentation(lifecycleStatus);
  const [pendingDecision, setPendingDecision] = useState<DecisionState | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const baseId = useId();
  const toggleId = `${baseId}-approval-details-toggle`;
  const panelId = `${baseId}-approval-details-panel`;
  const summarySourceItems =
    evidenceSections.length > 0
      ? evidenceSections.flatMap((section) => section.items)
      : [...argumentSummary, ...beforeState].flatMap((section) => section.items);
  const summaryText = useMemo(
    () => summarizeDetails(summarySourceItems),
    [summarySourceItems],
  );
  const risk = coerceString(args.risk) ?? "CHANGE";
  const overview = [
    { label: "Status", value: copy.title },
    { label: "Tool", value: prettifyToolName(toolName) },
    { label: "Risk", value: risk },
    { label: "Action ID", value: actionRequestId },
  ];

  useEffect(() => {
    if (lifecycleStatus !== "requested") {
      setPendingDecision(null);
    }
  }, [lifecycleStatus]);

  useEffect(() => {
    if (lifecycleStatus !== "requested") {
      setDetailsOpen(false);
    }
  }, [lifecycleStatus]);

  if (!actionRequestId) {
    return (
      <div className="mt-2 rounded-lg border border-border bg-surface/70 p-3 text-sm text-muted">
        Missing action request id.
      </div>
    );
  }

  const canAct = lifecycleStatus === "requested" && !pendingDecision;
  const receiptLabel = pendingDecision
    ? pendingDecision === "approving"
      ? "Approving"
      : "Denying"
    : getReceiptLabel(lifecycleStatus);
  const fallbackSections = [
    { title: "Overview", items: overview },
    ...beforeState,
    ...argumentSummary,
  ].filter((section) => section.items.length > 0);
  const sectionsForInline = evidenceSections.length > 0 ? evidenceSections : fallbackSections;

  if (lifecycleStatus === "requested") {
    return (
      <div className="mt-3 rounded-xl border border-accent/20 bg-accent/5 px-3 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-medium text-text">{activity}</div>
            <div className="mt-1 text-xs text-muted">
              {summaryText ?? copy.detail}
            </div>
          </div>
          <div
            className={[
              "inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em]",
              copy.badgeClassName,
            ].join(" ")}
          >
            <copy.Icon width={12} height={12} />
            <span className="leading-none">{receiptLabel}</span>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={!canAct}
            onClick={() => {
              setPendingDecision("approving");
              sendCommand({ type: "approve-action", actionRequestId });
            }}
            className="inline-flex h-8 items-center justify-center rounded-lg bg-accent px-3 text-xs font-medium text-white transition-colors hover:bg-accent/90 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pendingDecision === "approving" ? "Approving..." : "Approve"}
          </button>
          <button
            type="button"
            disabled={!canAct}
            onClick={() => {
              setPendingDecision("denying");
              sendCommand({ type: "deny-action", actionRequestId });
            }}
            className="inline-flex h-8 items-center justify-center rounded-lg border border-border bg-transparent px-3 text-xs font-medium text-text transition-all hover:bg-surface-2 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pendingDecision === "denying" ? "Denying..." : "Deny"}
          </button>
          <button
            type="button"
            id={toggleId}
            aria-expanded={detailsOpen}
            aria-controls={panelId}
            onClick={() => setDetailsOpen((value) => !value)}
            className="inline-flex h-8 items-center justify-center gap-1 rounded-lg border border-border bg-transparent px-3 text-xs font-medium text-muted transition hover:bg-surface-2 hover:text-text"
          >
            {detailsOpen ? "Hide details" : "Details"}
            <ChevronRightIcon
              width={14}
              height={14}
              className={[
                "transition-transform duration-200 motion-reduce:transition-none",
                detailsOpen ? "rotate-90" : "rotate-0",
              ].join(" ")}
              aria-hidden="true"
            />
          </button>
        </div>

        <div
          id={panelId}
          role="region"
          aria-labelledby={toggleId}
          hidden={!detailsOpen}
          className="mt-3"
        >
          {detailsOpen ? (
            <div className="rounded-xl border border-border bg-bg/15 px-3 py-3">
              <DetailSections sections={sectionsForInline} variant="inline" showEmptyState />
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return null;
}

export const RequestApprovalToolUI = makeAssistantToolUI({
  toolName: "request_approval",
  render: ({ args }: { args: Record<string, unknown>; result?: unknown; status?: unknown }) => {
    return <Actions args={args} />;
  },
});

export function ClaudeToolGroup({ children }: { children?: ReactNode }) {
  if (!children) return null;
  return <>{children}</>;
}

const TOOL_COPY: Record<string, { label: string; doing: string; done: string }> = {
  get_current_time: {
    label: "Current time",
    doing: "Checking the current time",
    done: "Checked the current time",
  },
  get_current_date: {
    label: "Today's date",
    doing: "Checking today's date",
    done: "Checked today's date",
  },
};

const STATUS_BADGE: Record<string, { label: string; className: string }> = {
  running: {
    label: "running",
    className: "bg-accent/10 text-accent",
  },
  complete: {
    label: "complete",
    className: "bg-surface-2/60 text-muted",
  },
  incomplete: {
    label: "failed",
    className: "bg-rose-50 text-rose-700",
  },
  "requires-action": {
    label: "requires-action",
    className: "bg-accent/15 text-accent",
  },
};

function humanizeToolName(toolName: string): string {
  return prettifyToolName(toolName);
}

function ToolLiveDot({ statusType }: { statusType: string }) {
  const dotClassName =
    statusType === "incomplete"
      ? "bg-rose-500"
      : statusType === "requires-action"
        ? "bg-amber-500"
        : "bg-accent";

  return (
    <span className="relative inline-flex h-2.5 w-2.5 shrink-0">
      {statusType === "running" ? (
        <span className="absolute inset-0 animate-ping rounded-full bg-accent/40" aria-hidden="true" />
      ) : null}
      <span className={["relative inline-flex h-2.5 w-2.5 rounded-full", dotClassName].join(" ")} aria-hidden="true" />
    </span>
  );
}

function isHiddenChangeToolValidationError(toolName: string, result: unknown): boolean {
  if (!toolName.startsWith("whm_") || toolName.startsWith("whm_preflight_")) {
    return false;
  }
  if (!result || typeof result !== "object") {
    return false;
  }

  const errorCode = (result as Record<string, unknown>).error_code;
  return (
    errorCode === "invalid_tool_arguments" ||
    errorCode === "preflight_required" ||
    errorCode === "preflight_mismatch"
  );
}

export function ClaudeToolFallback({ toolName, status, result, isError }: any) {
  const rawName = typeof toolName === "string" && toolName ? toolName : "tool";
  const copy =
    TOOL_COPY[rawName] ??
    ({
      label: humanizeToolName(rawName),
      doing: `Running ${humanizeToolName(rawName).toLowerCase()}`,
      done: `Finished ${humanizeToolName(rawName).toLowerCase()}`,
    } as const);

  const rawStatus = typeof status?.type === "string" ? status.type : undefined;
  const hasKnownStatus =
    rawStatus === "running" ||
    rawStatus === "complete" ||
    rawStatus === "incomplete" ||
    rawStatus === "requires-action";
  const statusType =
    isError === true
      ? "incomplete"
      : hasKnownStatus
        ? rawStatus
        : result !== undefined
          ? "complete"
          : "running";
  const badge = STATUS_BADGE[statusType] ?? STATUS_BADGE.incomplete;

  const activityText =
    statusType === "complete"
      ? copy.done
      : statusType === "incomplete"
        ? `Could not complete ${copy.label.toLowerCase()}`
        : statusType === "requires-action"
          ? `Waiting for approval before continuing ${copy.label.toLowerCase()}`
          : copy.doing;

  if (statusType === "complete") {
    return null;
  }

  if (isHiddenChangeToolValidationError(rawName, result)) {
    return null;
  }

  return (
    <div
      role="status"
      aria-label={`${copy.label} ${badge.label}`}
      className="flex items-center justify-between gap-2 rounded-md bg-surface/35 px-2 py-1.5 text-xs"
    >
      <div className="flex min-w-0 items-center gap-2 truncate text-muted">
        <ToolLiveDot statusType={statusType} />
        <span className="font-medium text-text">{copy.label}</span>
        <span className="mx-1.5 text-muted">-</span>
        <span>{activityText}</span>
      </div>
      <div className={["shrink-0 rounded px-1.5 py-0.5 text-[10px]", badge.className].join(" ")}>
        {badge.label}
      </div>
    </div>
  );
}
