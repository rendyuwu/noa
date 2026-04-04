"use client";

import { Code2, FolderOpen, Puzzle, Search } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type NavPlaceholder = {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  comingSoon: boolean;
};

const placeholderItems: NavPlaceholder[] = [
  { label: "Projects", icon: FolderOpen, comingSoon: true },
  { label: "Artifacts", icon: Puzzle, comingSoon: true },
  { label: "Code", icon: Code2, comingSoon: true },
];

export function ChatSidebarNav() {
  return (
    <div className="space-y-1 px-2 pb-2">
      {/* Search placeholder */}
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-2.5 rounded-lg border border-border/60 bg-bg/50 px-3 py-2 text-sm text-muted opacity-60 cursor-not-allowed"
            disabled
            aria-disabled="true"
            tabIndex={-1}
          >
            <Search className="size-4 shrink-0" />
            <input
              type="text"
              placeholder="Search"
              disabled
              className="w-full border-0 bg-transparent p-0 text-sm text-muted placeholder:text-muted outline-none cursor-not-allowed"
              tabIndex={-1}
            />
          </button>
        </TooltipTrigger>
        <TooltipContent side="right">Coming soon</TooltipContent>
      </Tooltip>

      {/* Feature nav placeholders */}
      <div className="mt-2 space-y-0.5">
        {placeholderItems.map((item) => (
          <Tooltip key={item.label}>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted/50 cursor-not-allowed"
                disabled
                aria-disabled="true"
                tabIndex={-1}
              >
                <item.icon className="size-4 shrink-0" />
                <span>{item.label}</span>
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">Coming soon</TooltipContent>
          </Tooltip>
        ))}
      </div>
    </div>
  );
}
