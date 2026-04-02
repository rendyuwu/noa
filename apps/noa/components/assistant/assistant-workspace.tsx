"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  ThreadPrimitive,
  makeAssistantToolUI,
  useAssistantApi,
  useAssistantState,
  useAssistantTransportSendCommand,
} from "@assistant-ui/react";
import { Bot, LoaderCircle, MessageSquarePlus, ShieldAlert } from "lucide-react";

import { getActiveThreadListItem } from "@/components/lib/runtime/assistant-thread-state";
import { useThreadHydration } from "@/components/lib/runtime/thread-hydration";

function ApprovalActions({ args }: { args: Record<string, unknown> }) {
  const sendCommand = useAssistantTransportSendCommand();
  const actionRequestId =
    typeof args.actionRequestId === "string" ? args.actionRequestId : "";
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
          onClick={() => sendCommand({ type: "approve-action", actionRequestId })}
        >
          Approve
        </button>
        <button
          type="button"
          className="rounded-xl border border-border bg-bg px-3 py-2 font-ui text-sm font-medium text-text"
          onClick={() => sendCommand({ type: "deny-action", actionRequestId })}
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

function ToolGroup({ children }: { children?: ReactNode }) {
  return (
    <div className="mt-3 rounded-2xl border border-dashed border-border bg-bg/70 p-3">
      <div className="font-ui text-xs uppercase tracking-[0.16em] text-muted">Tool activity</div>
      {children}
    </div>
  );
}

function ToolFallback({ toolName, status, argsText, result }: Record<string, unknown>) {
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

function UserMessage() {
  return (
    <MessagePrimitive.Root className="mb-3 flex justify-end">
      <div className="max-w-[85%] rounded-2xl bg-accent px-4 py-3 text-sm text-accent-foreground shadow-soft">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="mb-3">
      <div className="max-w-[92%] rounded-2xl border border-border bg-surface px-4 py-3 text-sm text-text shadow-soft">
        <MessagePrimitive.Parts components={{ ToolGroup, tools: { Fallback: ToolFallback } }} />
      </div>
    </MessagePrimitive.Root>
  );
}

function ThreadPanel() {
  const isHydrating = useThreadHydration().isHydrating;
  const isRunning = useAssistantState(({ thread }) => Boolean(thread?.isRunning));

  return (
    <ThreadPrimitive.Root className="flex min-h-[65vh] flex-col rounded-3xl border border-border bg-surface shadow-soft">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Bot className="size-4 text-accent" />
        <div className="text-sm font-medium text-text">Assistant workspace</div>
        {(isHydrating || isRunning) && (
          <div className="ml-auto inline-flex items-center gap-2 font-ui text-xs text-muted">
            <LoaderCircle className="size-3.5 animate-spin" />
            {isHydrating ? "Hydrating thread…" : "Assistant running…"}
          </div>
        )}
      </div>

      <ThreadPrimitive.Viewport className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <ThreadPrimitive.Empty>
          <div className="mx-auto max-w-xl rounded-3xl border border-dashed border-border bg-bg/70 p-8 text-center">
            <MessageSquarePlus className="mx-auto size-10 text-accent" />
            <h2 className="mt-4 text-2xl font-semibold tracking-[-0.02em] text-text">Start a new conversation</h2>
            <p className="mt-3 font-ui text-sm leading-6 text-muted">
              The NOA rewrite now includes a live assistant runtime with persisted threads, hydration, URL sync, and
              streaming transport over the same-origin API boundary.
            </p>
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
      </ThreadPrimitive.Viewport>

      <ComposerPrimitive.Root className="border-t border-border px-4 py-4">
        <div className="rounded-2xl border border-border bg-bg p-2 shadow-sm">
          <ComposerPrimitive.Input
            className="min-h-24 w-full resize-none border-0 bg-transparent px-3 py-2 font-ui text-sm text-text outline-none"
            placeholder="Ask NOA…"
          />
          <div className="mt-2 flex items-center justify-end gap-2">
            <ComposerPrimitive.Cancel
              type="button"
              className="rounded-xl border border-border bg-surface px-3 py-2 font-ui text-sm font-medium text-text"
            >
              Stop
            </ComposerPrimitive.Cancel>
            <ComposerPrimitive.Send
              type="submit"
              className="rounded-xl bg-accent px-3 py-2 font-ui text-sm font-semibold text-accent-foreground"
            >
              Send
            </ComposerPrimitive.Send>
          </div>
        </div>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
}

function ThreadSidebar() {
  const activeRemoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const threadIds = useAssistantState(({ threads }) => threads?.threadIds ?? []);

  const ThreadListItem = () => {
    const remoteId = useAssistantState(({ threadListItem }) => threadListItem.remoteId ?? null);
    const title = useAssistantState(({ threadListItem }) => threadListItem.title ?? null);
    const status = useAssistantState(({ threadListItem }) => threadListItem.status ?? "regular");
    const isActive = remoteId !== null && activeRemoteId === remoteId;

    return (
      <ThreadListItemPrimitive.Root className="mb-2">
        <div
          className={[
            "flex items-center gap-2 rounded-2xl border px-3 py-2 transition",
            isActive ? "border-accent bg-accent/8" : "border-border bg-bg/70 hover:bg-surface-2",
          ].join(" ")}
        >
          <ThreadListItemPrimitive.Trigger className="min-w-0 flex-1 text-left">
            <span className="block truncate font-ui text-sm font-medium text-text">
              {title && title.trim() ? title : "Untitled thread"}
            </span>
            <span className="mt-1 block font-ui text-xs text-muted">{status === "archived" ? "Archived" : "Active"}</span>
          </ThreadListItemPrimitive.Trigger>
          <ThreadListItemPrimitive.Delete className="rounded-lg border border-border bg-surface px-2 py-1 font-ui text-xs text-muted">
            Delete
          </ThreadListItemPrimitive.Delete>
        </div>
      </ThreadListItemPrimitive.Root>
    );
  };

  return (
    <ThreadListPrimitive.Root className="rounded-3xl border border-border bg-surface p-4 shadow-soft">
      <div className="flex items-center gap-2">
        <MessageSquarePlus className="size-4 text-accent" />
        <div className="text-sm font-medium text-text">Threads</div>
      </div>
      <p className="mt-2 font-ui text-sm leading-6 text-muted">
        Persisted thread list backed by the browser-safe `/api/threads` contract.
      </p>
      <ThreadListPrimitive.New className="mt-4 inline-flex w-full items-center justify-center rounded-2xl bg-accent px-4 py-3 font-ui text-sm font-semibold text-accent-foreground">
        New thread
      </ThreadListPrimitive.New>
      <div className="mt-4 max-h-[55vh] overflow-y-auto pr-1">
        {threadIds.length > 0 ? (
          <ThreadListPrimitive.Items components={{ ThreadListItem }} />
        ) : (
          <div className="rounded-2xl border border-dashed border-border px-4 py-6 font-ui text-sm text-muted">
            No threads yet. Start the first conversation.
          </div>
        )}
      </div>
    </ThreadListPrimitive.Root>
  );
}

function RouteThreadSync({ routeThreadId }: { routeThreadId?: string | null }) {
  const api = useAssistantApi();
  const activeRemoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const [routeError, setRouteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        if (!routeThreadId) {
          setRouteError(null);
          return;
        }

        if (activeRemoteId === routeThreadId) {
          setRouteError(null);
          return;
        }

        await api.threads().switchToThread(routeThreadId);
        if (!cancelled) {
          setRouteError(null);
        }
      } catch (error) {
        console.error("Failed to switch to assistant thread route", error);
        if (!cancelled) {
          setRouteError("This chat link is invalid or no longer available.");
        }
        try {
          await api.threads().switchToNewThread();
        } catch {}
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeRemoteId, api, routeThreadId]);

  if (!routeError) {
    return null;
  }

  return (
    <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 font-ui text-sm text-amber-800">
      <div className="flex items-center gap-2">
        <ShieldAlert className="size-4" />
        {routeError}
      </div>
    </div>
  );
}

export function AssistantWorkspace({ threadId }: { threadId?: string | null }) {
  return (
    <section className="space-y-4">
      <RequestApprovalToolUI />
      <RouteThreadSync routeThreadId={threadId} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
        <ThreadSidebar />
        <ThreadPanel />
      </div>
    </section>
  );
}
