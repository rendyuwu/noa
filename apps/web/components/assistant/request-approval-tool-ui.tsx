"use client";

import { makeAssistantToolUI, useAssistantState } from "@assistant-ui/react";

import {
  extractLatestCanonicalActionRequests,
  type AssistantActionLifecycleStatus,
} from "@/components/assistant/approval-state";
import type { ReactNode } from "react";

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

function getLifecycleCopy(status: AssistantActionLifecycleStatus) {
  switch (status) {
    case "requested":
      return {
        title: "Approval requested",
        detail: "Use the live approval panel above the composer to decide this request.",
      };
    case "approved":
      return {
        title: "Approval recorded",
        detail: "This request was approved and is queued to execute.",
      };
    case "executing":
      return {
        title: "Executing approved action",
        detail: "The approval step is complete and execution is in progress.",
      };
    case "finished":
      return {
        title: "Approved action finished",
        detail: "This request resolved successfully.",
      };
    case "failed":
      return {
        title: "Approved action failed",
        detail: "Approval succeeded, but the follow-up execution failed.",
      };
    case "denied":
      return {
        title: "Action request denied",
        detail: "This request reached a terminal denied state.",
      };
  }
}

function Actions({ args }: { args: Record<string, unknown> }) {
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
  const copy = getLifecycleCopy(lifecycleStatus);

  if (!actionRequestId) {
    return (
      <div className="mt-2 rounded-lg border border-border bg-surface/70 p-3 text-sm text-muted">
        Missing action request id.
      </div>
    );
  }

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.035),0_0_0_0.5px_rgba(0,0,0,0.08)]">
      <div className="flex items-start justify-between gap-3 border-b border-border bg-surface-2 px-4 py-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-text">{copy.title}</div>
          <div className="mt-0.5 text-xs text-muted">
            {copy.detail}
          </div>
        </div>
      </div>
      <div className="px-4 py-3">
        <div className="rounded-lg border border-border bg-bg/40 px-3 py-2.5 text-sm text-text">
          {activity}
        </div>

        {beforeState.length ? (
          <div className="mt-3 rounded-lg border border-border bg-bg/40 px-3 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">Before state</div>
            <dl className="mt-2 space-y-1.5 text-sm">
              {beforeState.map((item) => (
                <div key={`${item.label}-${item.value}`} className="grid grid-cols-[9rem_minmax(0,1fr)] gap-3">
                  <dt className="text-muted">{item.label}</dt>
                  <dd className="min-w-0 break-words text-text">{item.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null}

        {argumentSummary.length ? (
          <div className="mt-3 rounded-lg border border-border bg-bg/40 px-3 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">Requested change</div>
            <dl className="mt-2 space-y-1.5 text-sm">
              {argumentSummary.map((item) => (
                <div key={`${item.label}-${item.value}`} className="grid grid-cols-[9rem_minmax(0,1fr)] gap-3">
                  <dt className="text-muted">{item.label}</dt>
                  <dd className="min-w-0 break-words text-text">{item.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null}

        <div className="mt-3 rounded-lg border border-dashed border-border bg-surface/50 px-3 py-2 text-xs text-muted">
          Transcript cards now show approval history only. Use the canonical approval panel to approve or deny live requests.
        </div>
      </div>
    </div>
  );
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
    label: "incomplete",
    className: "bg-red-50 text-red-800",
  },
  "requires-action": {
    label: "requires-action",
    className: "bg-accent/15 text-accent",
  },
};

function humanizeToolName(toolName: string): string {
  return prettifyToolName(toolName);
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

  return (
    <div className="flex items-center justify-between gap-2 rounded-md bg-surface/40 px-2 py-1.5 text-xs">
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
