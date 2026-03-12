"use client";

import { makeAssistantToolUI, useAssistantTransportSendCommand } from "@assistant-ui/react";
import { CheckIcon, Cross2Icon } from "@radix-ui/react-icons";
import type { ReactNode } from "react";

function coerceString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function Actions({ args }: { args: Record<string, unknown> }) {
  const sendCommand = useAssistantTransportSendCommand();
  const actionRequestId = coerceString(args.actionRequestId) ?? "";
  const toolName = coerceString(args.toolName) ?? "unknown";

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
          <div className="text-sm font-semibold text-text">
            Approval requested
          </div>
          <div className="mt-0.5 text-xs text-muted">
            Tool: <code className="text-[11px]">{toolName}</code>
          </div>
        </div>
      </div>
      <div className="px-4 py-3">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => sendCommand({ type: "approve-action", actionRequestId })}
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg bg-accent px-3 text-xs font-medium text-white transition-colors hover:bg-accent/90 active:scale-[0.98]"
          >
            <CheckIcon width={16} height={16} />
            Approve
          </button>
          <button
            type="button"
            onClick={() => sendCommand({ type: "deny-action", actionRequestId })}
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-border bg-transparent px-3 text-xs font-medium text-text transition-all hover:bg-surface-2 active:scale-[0.98]"
          >
            <Cross2Icon width={16} height={16} />
            Deny
          </button>
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
    className: "bg-emerald-50 text-emerald-800",
  },
  incomplete: {
    label: "incomplete",
    className: "bg-red-50 text-red-800",
  },
  "requires-action": {
    label: "requires-action",
    className: "bg-amber-50 text-amber-800",
  },
};

function humanizeToolName(toolName: string): string {
  return toolName
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
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
    hasKnownStatus
      ? rawStatus
      : isError
        ? "incomplete"
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

  if (statusType === "complete" && !isError) {
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
