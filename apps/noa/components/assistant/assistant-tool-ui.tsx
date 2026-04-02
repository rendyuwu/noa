"use client";

import type { ReactNode } from "react";
import { makeAssistantToolUI, useAssistantTransportSendCommand } from "@assistant-ui/react";

type ApprovalCommand = {
  actionRequestId: string;
  type: "approve-action" | "deny-action";
};

function sendApprovalCommand(
  sendCommand: (command: ApprovalCommand) => void,
  type: ApprovalCommand["type"],
  actionRequestId: string,
) {
  sendCommand({ type, actionRequestId });
}

function ApprovalActions({ args }: { args: Record<string, unknown> }) {
  const sendCommand = useAssistantTransportSendCommand() as unknown as (command: ApprovalCommand) => void;
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
        <button
          type="button"
          className="rounded-xl bg-accent px-3 py-2 font-ui text-sm font-semibold text-accent-foreground"
          onClick={() => sendApprovalCommand(sendCommand, "approve-action", actionRequestId)}
        >
          Approve
        </button>
        <button
          type="button"
          className="rounded-xl border border-border bg-bg px-3 py-2 font-ui text-sm font-medium text-text"
          onClick={() => sendApprovalCommand(sendCommand, "deny-action", actionRequestId)}
        >
          Deny
        </button>
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
    <div className="mt-3 rounded-2xl border border-dashed border-border bg-bg/70 p-3">
      <div className="font-ui text-xs uppercase tracking-[0.16em] text-muted">Tool activity</div>
      {children}
    </div>
  );
}

export function ToolFallback({ toolName, status, argsText, result }: Record<string, unknown>) {
  const statusText = typeof status === "object" && status && "type" in status ? String(status.type) : "unknown";
  return (
    <div className="mt-3 rounded-2xl border border-border bg-bg/70 p-3">
      <strong className="text-sm text-text">{typeof toolName === "string" ? toolName : "tool"}</strong>
      <div className="mt-1 font-ui text-xs text-muted">Status: {statusText}</div>
      {typeof argsText === "string" && argsText ? (
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-xl bg-surface p-3 font-ui text-xs text-text">
          {argsText}
        </pre>
      ) : null}
      {result !== undefined ? (
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-xl bg-surface p-3 font-ui text-xs text-text">
          {JSON.stringify(result, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
