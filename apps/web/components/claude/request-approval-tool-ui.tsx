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
  return (
    <div className="mt-3 rounded-xl border border-border bg-surface/60 p-3 shadow-sm">
      <div className="text-[0.7rem] uppercase tracking-wide text-muted">
        Tool activity
      </div>
      <div className="mt-2">{children}</div>
    </div>
  );
}

export function ClaudeToolFallback({ toolName, status, argsText, result, isError }: any) {
  const name = typeof toolName === "string" && toolName ? toolName : "tool";
  const statusText = typeof status?.type === "string" ? status.type : "unknown";

  return (
    <div className="rounded-xl border border-border bg-surface p-3 text-sm shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 font-medium text-text">{name}</div>
        <div
          className={[
            "shrink-0 rounded-md px-2 py-0.5 text-[11px]",
            isError ? "bg-red-50 text-red-800" : "bg-surface-2 text-muted",
          ].join(" ")}
        >
          {statusText}
        </div>
      </div>

      {argsText ? (
        <pre className="mt-2 max-h-48 overflow-auto rounded-lg border border-border bg-surface-2 p-2 text-[12px] text-text">
          {argsText}
        </pre>
      ) : null}

      {result !== undefined ? (
        <pre className="mt-2 max-h-64 overflow-auto rounded-lg border border-border bg-surface p-2 text-[12px] text-text">
          {typeof result === "string" ? result : JSON.stringify(result, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
