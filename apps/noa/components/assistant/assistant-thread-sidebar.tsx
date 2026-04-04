"use client";

import { ThreadListItemPrimitive, ThreadListPrimitive, useAssistantState } from "@assistant-ui/react";
import { MessageSquarePlus } from "lucide-react";

import { getActiveThreadListItem } from "@/components/lib/runtime/assistant-thread-state";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

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
        Your saved conversations. Select a thread to continue, or start a new one.
      </p>
      <ThreadListPrimitive.New asChild>
        <Button className="mt-4 w-full rounded-2xl font-ui text-sm font-semibold">New thread</Button>
      </ThreadListPrimitive.New>
      <ScrollArea className="mt-4 max-h-[60vh]">
        {threadIds.length > 0 ? (
          <ThreadListPrimitive.Items components={{ ThreadListItem }} />
        ) : (
          <div className="rounded-2xl border border-dashed border-border px-4 py-6 font-ui text-sm text-muted">
            No threads yet. Start the first conversation.
          </div>
        )}
      </ScrollArea>
    </ThreadListPrimitive.Root>
  );
}
