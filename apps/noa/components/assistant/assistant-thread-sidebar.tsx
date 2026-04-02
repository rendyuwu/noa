"use client";

import { ThreadListItemPrimitive, ThreadListPrimitive, useAssistantState } from "@assistant-ui/react";
import { MessageSquarePlus } from "lucide-react";

import { getActiveThreadListItem } from "@/components/lib/runtime/assistant-thread-state";

export function ThreadSidebar() {
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
