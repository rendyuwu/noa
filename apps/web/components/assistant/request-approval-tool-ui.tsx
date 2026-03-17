"use client";

import { useEffect, useMemo, useState } from "react";

import {
  makeAssistantToolUI,
  useAssistantState,
  useAssistantTransportSendCommand,
} from "@assistant-ui/react";
import { ChevronRightIcon } from "@radix-ui/react-icons";
import type { ReactNode } from "react";

import {
  toggleAssistantDetailSheet,
  useAssistantDetailSheet,
} from "@/components/assistant/assistant-detail-sheet-store";
import { getApprovalLifecyclePresentation } from "@/components/assistant/approval-lifecycle-ui";
import {
  extractLatestCanonicalActionRequests,
} from "@/components/assistant/approval-state";

function coerceString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function coerceRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

type DetailItem = {
  label: string;
  value: string;
};

function coerceDetailItems(value: unknown): DetailItem[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    const item = coerceRecord(entry);
    const label = coerceString(item?.label);
    const rawValue = item?.value;
    if (!label) return [];
    if (typeof rawValue === "string") return [{ label, value: rawValue }];
    if (typeof rawValue === "number" || typeof rawValue === "boolean") {
      return [{ label, value: String(rawValue) }];
    }
    return [];
  });
}

function prettifyToolName(value: string): string {
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

type DecisionState = "approving" | "denying";

function summarizeDetails(items: DetailItem[]): string | null {
  const preview = items
    .slice(0, 3)
    .map((item) => `${item.label}: ${item.value}`)
    .join(" · ");
  return preview || null;
}

function getReceiptLabel(lifecycleStatus: string): string {
  switch (lifecycleStatus) {
    case "finished":
      return "Completed";
    case "failed":
      return "Failed";
    case "denied":
      return "Denied";
    case "approved":
    case "executing":
      return "Executing";
    default:
      return "Approval needed";
  }
}

function Actions({ args }: { args: Record<string, unknown> }) {
  const sendCommand = useAssistantTransportSendCommand();
  const actionRequestId = coerceString(args.actionRequestId) ?? "";
  const toolName = coerceString(args.toolName) ?? "unknown";
  const activity = coerceString(args.activity) ?? `Run ${prettifyToolName(toolName).toLowerCase()}`;
  const beforeState = coerceDetailItems(args.beforeState);
  const argumentSummary = coerceDetailItems(args.argumentSummary);
  const threadMessages = useAssistantState(({ thread }: any) => thread?.messages);
  const lifecycleStatus =
    extractLatestCanonicalActionRequests(threadMessages)?.find(
      (request) => request.actionRequestId === actionRequestId,
    )?.lifecycleStatus ?? "requested";
  const copy = getApprovalLifecyclePresentation(lifecycleStatus);
  const detailSheet = useAssistantDetailSheet();
  const hasDetails = beforeState.length > 0 || argumentSummary.length > 0;
  const [pendingDecision, setPendingDecision] = useState<DecisionState | null>(null);
  const detailKey = `approval:${actionRequestId}`;
  const summaryText = useMemo(
    () => summarizeDetails(argumentSummary) ?? summarizeDetails(beforeState),
    [argumentSummary, beforeState],
  );

  useEffect(() => {
    if (lifecycleStatus !== "requested") {
      setPendingDecision(null);
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

  const openDetails = () => {
    toggleAssistantDetailSheet({
      open: true,
      key: detailKey,
      kind: "approval",
      title: activity,
      subtitle: `${copy.title}${summaryText ? ` · ${summaryText}` : ""}`,
      badge: copy.badge,
      badgeClassName: copy.badgeClassName,
      sections: [
        { title: "Before state", items: beforeState },
        { title: "Requested change", items: argumentSummary },
      ].filter((section) => section.items.length > 0),
    });
  };

  if (lifecycleStatus === "requested") {
    return (
      <div className="mt-3 rounded-xl border border-accent/20 bg-accent/5 px-3 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-medium text-text">{activity}</div>
            <div className="mt-1 text-xs text-muted">
              {summaryText ?? "This change needs approval before execution can continue."}
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
          {hasDetails ? (
            <button
              type="button"
              onClick={openDetails}
              className="inline-flex h-8 items-center justify-center gap-1 rounded-lg border border-border bg-transparent px-3 text-xs font-medium text-muted transition hover:bg-surface-2 hover:text-text"
            >
              {detailSheet.open && detailSheet.key === detailKey ? "Hide details" : "Details"}
              <ChevronRightIcon width={14} height={14} />
            </button>
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
    className: "bg-surface-2 text-muted",
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

  if (statusType !== "incomplete") {
    return null;
  }

  if (isHiddenChangeToolValidationError(rawName, result)) {
    return null;
  }

  return (
    <div className="flex items-center justify-between gap-2 rounded-md bg-surface/35 px-2 py-1.5 text-xs">
      <div className="min-w-0 truncate text-muted">
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
