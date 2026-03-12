"use client";

import type { ReactNode } from "react";

import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  ThreadPrimitive,
  makeAssistantToolUI,
  useAssistantTransportSendCommand,
} from "@assistant-ui/react";

function ApprovalActions({ args }: { args: Record<string, unknown> }) {
  const sendCommand = useAssistantTransportSendCommand();
  const actionRequestId = typeof args.actionRequestId === "string" ? args.actionRequestId : "";
  const toolName = typeof args.toolName === "string" ? args.toolName : "unknown";

  if (!actionRequestId) {
    return <div className="muted">Missing action request id.</div>;
  }

  return (
    <div className="panel" style={{ padding: 10, marginTop: 6 }}>
      <strong>Approval requested</strong>
      <p className="muted" style={{ marginTop: 4 }}>
        Tool: <code>{toolName}</code>
      </p>
      <div className="row">
        <button
          className="button button-primary"
          onClick={() => sendCommand({ type: "approve-action", actionRequestId })}
          type="button"
        >
          Approve
        </button>
        <button
          className="button button-danger"
          onClick={() => sendCommand({ type: "deny-action", actionRequestId })}
          type="button"
        >
          Deny
        </button>
      </div>
    </div>
  );
}

const RequestApprovalToolUI = makeAssistantToolUI({
  toolName: "request_approval",
  render: ({ args }: { args: Record<string, unknown> }) => {
    return <ApprovalActions args={args} />;
  },
});

const ToolGroup = ({ children }: { children?: ReactNode }) => {
  return (
    <div className="panel" style={{ padding: 8, marginTop: 6, borderStyle: "dashed" }}>
      <div className="muted" style={{ marginBottom: 6 }}>
        Tool activity
      </div>
      {children}
    </div>
  );
};

const ToolFallback = ({ toolName, status, argsText, result }: any) => {
  const statusText = typeof status?.type === "string" ? status.type : "unknown";
  return (
    <div className="panel" style={{ padding: 8, marginTop: 6 }}>
      <strong>{toolName ?? "tool"}</strong>
      <div className="muted">Status: {statusText}</div>
      {argsText ? <pre style={{ margin: "6px 0", whiteSpace: "pre-wrap" }}>{argsText}</pre> : null}
      {result !== undefined ? (
        <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(result, null, 2)}</pre>
      ) : null}
    </div>
  );
};

const UserMessage = () => {
  return (
    <MessagePrimitive.Root className="row" style={{ justifyContent: "flex-end", marginBottom: 10 }}>
      <div className="panel bg-surface-2" style={{ padding: "8px 10px", maxWidth: "80%" }}>
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
};

const AssistantMessage = () => {
  return (
    <MessagePrimitive.Root style={{ marginBottom: 10 }}>
      <div className="panel" style={{ padding: "8px 10px", maxWidth: "80%" }}>
        <MessagePrimitive.Parts components={{ ToolGroup, tools: { Fallback: ToolFallback } }} />
      </div>
    </MessagePrimitive.Root>
  );
};

function ThreadPanel() {
  return (
    <ThreadPrimitive.Root className="panel" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
      <ThreadPrimitive.Viewport style={{ flex: 1, overflow: "auto", padding: 12 }}>
        <ThreadPrimitive.Empty>
          <p className="muted">Start a new conversation.</p>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
      </ThreadPrimitive.Viewport>

      <ComposerPrimitive.Root
        className="row"
        style={{
          borderTop: "1px solid var(--line)",
          padding: 10,
          alignItems: "center",
        }}
      >
        <ComposerPrimitive.Input className="input" placeholder="Ask NOA..." />
        <ComposerPrimitive.Cancel className="button" type="button">
          Stop
        </ComposerPrimitive.Cancel>
        <ComposerPrimitive.Send className="button button-primary" type="submit">
          Send
        </ComposerPrimitive.Send>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
}

function ThreadSidebar() {
  const ThreadListItem = () => (
    <ThreadListItemPrimitive.Root style={{ marginBottom: 6 }}>
      <div className="row" style={{ alignItems: "center" }}>
        <ThreadListItemPrimitive.Trigger className="button" style={{ flex: 1, textAlign: "left" }}>
          <ThreadListItemPrimitive.Title fallback="Untitled" />
        </ThreadListItemPrimitive.Trigger>
        <ThreadListItemPrimitive.Archive className="button">Archive</ThreadListItemPrimitive.Archive>
        <ThreadListItemPrimitive.Delete className="button button-danger">Delete</ThreadListItemPrimitive.Delete>
      </div>
    </ThreadListItemPrimitive.Root>
  );

  return (
    <ThreadListPrimitive.Root className="panel" style={{ padding: 10 }}>
      <ThreadListPrimitive.New className="button button-primary" style={{ width: "100%" }}>
        New Thread
      </ThreadListPrimitive.New>
      <div style={{ height: 10 }} />
      <ThreadListPrimitive.Items components={{ ThreadListItem }} />
    </ThreadListPrimitive.Root>
  );
}

export function AssistantWorkspace() {
  return (
    <section
      className="assistant-grid"
      style={{
        display: "grid",
        gridTemplateColumns: "280px minmax(0,1fr)",
        gap: 12,
        minHeight: "calc(100vh - 96px)",
      }}
    >
      <RequestApprovalToolUI />
      <ThreadSidebar />
      <ThreadPanel />
    </section>
  );
}
