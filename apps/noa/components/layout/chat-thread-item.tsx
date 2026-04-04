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
    <ThreadListItemPrimitive.Root className="group/thread relative mb-0.5">
      <ThreadListItemPrimitive.Trigger
        className={[
          "block w-full truncate rounded-lg px-3 py-2 pr-8 text-left font-ui text-sm transition",
          isActive
            ? "bg-surface-2 font-medium text-text"
            : "text-muted hover:bg-surface-2/60 hover:text-text",
          isUntitled ? "italic" : "",
        ].join(" ")}
      >
        {displayTitle}
      </ThreadListItemPrimitive.Trigger>

      {/* Hover action menu */}
      <div
        className={[
          "absolute right-1 top-1/2 -translate-y-1/2",
          menuOpen ? "opacity-100" : "opacity-0 group-hover/thread:opacity-100",
          "transition-opacity",
        ].join(" ")}
      >
        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-6 rounded-md text-muted hover:bg-surface-2 hover:text-text"
              aria-label="Thread actions"
              onClick={(e) => e.stopPropagation()}
            >
              <MoreHorizontal className="size-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="right" align="start">
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
