"use client";

import { useId, useState, type ReactNode } from "react";
import { makeAssistantToolUI } from "@assistant-ui/react";

import { useApprovalSendCommand } from "./assistant-types";
import { Button } from "@/components/ui/button";

function ApprovalActions({ args }: { args: Record<string, unknown> }) {
  const sendCommand = useApprovalSendCommand();
  const actionRequestId = typeof args.actionRequestId === "string" ? args.actionRequestId : "";
  const toolName = typeof args.toolName === "string" ? args.toolName : "unknown";

  if (!actionRequestId) {
    return <div className="font-ui text-sm text-muted">Missing action request id.</div>;
  }

  return (
    <div className="mt-3 rounded-2xl border border-border bg-surface p-4">
      <strong className="text-sm text-text">Approval requested</strong>
      <p className="mt-2 font-ui text-sm text-muted">
        Tool: <code>{toolName}</code>
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          className="rounded-xl font-ui text-sm font-semibold"
          onClick={() => sendCommand({ type: "approve-action", actionRequestId })}
        >
          Approve
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="rounded-xl font-ui text-sm font-medium"
          onClick={() => sendCommand({ type: "deny-action", actionRequestId })}
        >
          Deny
        </Button>
      </div>
    </div>
  );
}

export const RequestApprovalToolUI = makeAssistantToolUI({
  toolName: "request_approval",
  render: ({ args }: { args: Record<string, unknown> }) => {
    return <ApprovalActions args={args} />;
  },
});

export function ToolGroup({ children }: { children?: ReactNode }) {
  return (
    <div className="mt-3 rounded-2xl border border-border/60 bg-bg/50 px-3 py-2">
      <div className="font-ui text-[11px] uppercase tracking-[0.16em] text-muted/80">Tool activity</div>
      {children}
    </div>
  );
}

function getToolStatusLabel(status: unknown) {
  if (typeof status === "object" && status && "type" in status) {
    const rawStatus = String((status as { type?: unknown }).type ?? "").toLowerCase();

    if (rawStatus === "running" || rawStatus === "in_progress" || rawStatus === "streaming") {
      return "Running…";
    }

    if (rawStatus === "complete" || rawStatus === "completed" || rawStatus === "done" || rawStatus === "success") {
      return "Completed";
    }

    if (rawStatus) {
      return rawStatus.replaceAll("_", " ");
    }
  }

  return "Status unknown";
}

export function ToolFallback({ toolName, status, argsText, result }: Record<string, unknown>) {
  const [showDetails, setShowDetails] = useState(false);
  const baseId = useId();
  const toggleId = `${baseId}-tool-details-toggle`;
  const panelId = `${baseId}-tool-details-panel`;
  const statusText = getToolStatusLabel(status);
  const toolLabel = typeof toolName === "string" ? toolName : "tool";

  return (
    <div className="mt-3 rounded-2xl border border-border/60 bg-bg/50 px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <strong className="block truncate text-sm text-text">{toolLabel}</strong>
          <div className="mt-0.5 font-ui text-xs text-muted">{statusText}</div>
        </div>
        <Button
          type="button"
          id={toggleId}
          variant="ghost"
          size="sm"
          aria-expanded={showDetails}
          aria-controls={panelId}
          className="h-8 rounded-lg px-2 font-ui text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
          onClick={() => setShowDetails((value) => !value)}
        >
          {showDetails ? "Hide details" : "Show details"}
        </Button>
      </div>
      {showDetails ? (
        <div id={panelId} role="region" aria-labelledby={toggleId} className="mt-2 space-y-2">
          {typeof argsText === "string" && argsText ? (
            <div className="space-y-1">
              <div className="font-ui text-[11px] uppercase tracking-[0.08em] text-muted">Arguments</div>
              <pre className="overflow-x-auto whitespace-pre-wrap rounded-xl bg-surface p-3 font-ui text-xs text-text">
                {argsText}
              </pre>
            </div>
          ) : null}
          {result !== undefined ? (
            <div className="space-y-1">
              <div className="font-ui text-[11px] uppercase tracking-[0.08em] text-muted">Result</div>
              <pre className="overflow-x-auto whitespace-pre-wrap rounded-xl bg-surface p-3 font-ui text-xs text-text">
                {JSON.stringify(result, null, 2)}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
