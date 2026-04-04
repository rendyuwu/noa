"use client";

import { useAssistantState } from "@assistant-ui/react";
import { Share } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function ThreadHeader() {
  const title = useAssistantState(({ threadListItem }) => threadListItem?.title ?? null);
  const isEmpty = useAssistantState(({ thread }) => {
    const msgs = thread?.messages;
    return !msgs || msgs.length === 0;
  });

  // Don't show header on empty threads (empty state handles this)
  if (isEmpty) return null;

  const displayTitle = title && title.trim() ? title : "New conversation";

  return (
    <div className="hidden items-center justify-between border-b border-border/40 bg-bg/80 px-4 py-2 backdrop-blur md:flex">
      <div className="min-w-0 flex-1">
        <h1 className="truncate font-ui text-sm font-medium text-text">{displayTitle}</h1>
      </div>
      <div className="flex items-center gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-lg text-muted hover:text-text"
              disabled
              aria-label="Share"
            >
              <Share className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Coming soon</TooltipContent>
        </Tooltip>
        <ThemeToggle />
      </div>
    </div>
  );
}
