"use client";

import { useState } from "react";
import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { ThreadListItemPrimitive, useAssistantState } from "@assistant-ui/react";

import { getActiveThreadListItem } from "@/components/lib/runtime/assistant-thread-state";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function ChatThreadItem() {
  const activeRemoteId = useAssistantState(
    ({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null,
  );
  const remoteId = useAssistantState(
    ({ threadListItem }) => threadListItem.remoteId ?? null,
  );
  const title = useAssistantState(
    ({ threadListItem }) => threadListItem.title ?? null,
  );
  const isActive = remoteId !== null && activeRemoteId === remoteId;

  const [menuOpen, setMenuOpen] = useState(false);

  const displayTitle = title && title.trim() ? title : "Untitled thread";
  const isUntitled = !title || !title.trim();

  return (
    <ThreadListItemPrimitive.Root className="group/thread mb-1 overflow-hidden rounded-xl">
      <div className="flex min-w-0 items-center gap-1">
        <ThreadListItemPrimitive.Trigger
          className={[
            "min-w-0 flex-1 truncate rounded-xl border border-transparent px-3 py-2.5 text-left font-ui text-sm transition",
            isActive
              ? "border-border bg-surface-2 font-medium text-text shadow-soft"
              : "text-muted hover:bg-surface-2/60 hover:text-text",
            isUntitled ? "italic" : "",
          ].join(" ")}
        >
          {displayTitle}
        </ThreadListItemPrimitive.Trigger>

        <DropdownMenu modal={false} open={menuOpen} onOpenChange={setMenuOpen}>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 shrink-0 rounded-md text-muted hover:bg-surface-2 hover:text-text"
              aria-label="Thread actions"
              onClick={(e) => e.stopPropagation()}
            >
              <MoreHorizontal className="size-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="bottom" align="end">
            <DropdownMenuItem disabled className="gap-2 text-muted">
              <Pencil className="size-3.5" />
              Rename
            </DropdownMenuItem>
            <ThreadListItemPrimitive.Delete asChild>
              <DropdownMenuItem className="gap-2 text-destructive focus:text-destructive">
                <Trash2 className="size-3.5" />
                Delete
              </DropdownMenuItem>
            </ThreadListItemPrimitive.Delete>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </ThreadListItemPrimitive.Root>
  );
}
