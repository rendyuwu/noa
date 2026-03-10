"use client";

import Link from "next/link";
import type { FC } from "react";

import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
} from "@assistant-ui/react";
import { PlusIcon, TrashIcon } from "@radix-ui/react-icons";

import { formatClaudeGreetingName } from "@/components/claude/claude-greeting";
import { clearAuth, getAuthUser } from "@/components/lib/auth-store";

function DisabledNavItem({ label }: { label: string }) {
  return (
    <button
      type="button"
      disabled
      aria-disabled="true"
      title="Coming soon"
      className="flex w-full items-center justify-start gap-3 rounded-lg px-3 py-2 font-ui text-sm text-[#6b6a68] opacity-70 transition hover:bg-[#ffffff80] hover:text-[#1a1a18] disabled:pointer-events-none dark:text-[#9a9893] dark:hover:bg-[#1f1e1b]/60 dark:hover:text-[#eee]"
    >
      {label}
    </button>
  );
}

const ThreadListItem: FC<{ onSelect?: () => void }> = ({ onSelect }) => {
  return (
    <ThreadListItemPrimitive.Root className="group px-2">
      <div className="flex items-center gap-2 rounded-lg px-2 py-2 transition hover:bg-[#ffffff80] dark:hover:bg-[#1f1e1b]/60">
        <ThreadListItemPrimitive.Trigger
          onClick={onSelect}
          className="min-w-0 flex-1 rounded-md text-left text-sm text-[#1a1a18] outline-none focus-visible:ring-2 focus-visible:ring-[#ae5630]/35 focus-visible:ring-offset-2 focus-visible:ring-offset-[#F5F5F0] dark:text-[#eee] dark:focus-visible:ring-offset-[#2b2a27]"
        >
          <span className="block truncate">
            <ThreadListItemPrimitive.Title fallback="Untitled" />
          </span>
        </ThreadListItemPrimitive.Trigger>

        <ThreadListItemPrimitive.Delete
          className="flex h-7 w-7 items-center justify-center rounded-md text-[#6b6a68] opacity-0 transition hover:bg-[#f5f5f0] hover:text-[#1a1a18] group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#ae5630]/35 focus-visible:ring-offset-2 focus-visible:ring-offset-[#F5F5F0] dark:text-[#9a9893] dark:hover:bg-[#393937] dark:hover:text-[#eee] dark:focus-visible:ring-offset-[#2b2a27]"
          aria-label="Delete thread"
        >
          <TrashIcon width={16} height={16} />
        </ThreadListItemPrimitive.Delete>
      </div>
    </ThreadListItemPrimitive.Root>
  );
};

export function ClaudeThreadList({ onSelectThread }: { onSelectThread?: () => void }) {
  const user = getAuthUser();
  const name = formatClaudeGreetingName(user);
  const initial = name.trim().slice(0, 1).toUpperCase() || "U";
  const secondary = user?.email?.trim() || user?.roles?.join(", ") || "Signed in";

  return (
    <ThreadListPrimitive.Root className="flex h-full flex-col bg-[#F5F5F0] font-serif dark:bg-[#2b2a27]">
      <div className="px-4 pt-4">
        <ThreadListPrimitive.New
          onClick={onSelectThread}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#ae5630] px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#c4633a] active:scale-[0.99]"
        >
          <PlusIcon width={16} height={16} />
          New chat
        </ThreadListPrimitive.New>

        <div className="mt-3 px-2">
          <DisabledNavItem label="Search" />
          <DisabledNavItem label="Customize" />
          <DisabledNavItem label="Projects" />
          <DisabledNavItem label="Artifacts" />
          <DisabledNavItem label="Code" />
        </div>
      </div>

      <div className="mt-3 flex-1 overflow-y-auto pb-3">
        <ThreadListPrimitive.Items components={{ ThreadListItem: (props: any) => <ThreadListItem {...props} onSelect={onSelectThread} /> }} />
      </div>

      <div className="border-[#00000015] border-t px-4 py-4 dark:border-[#6c6a6040]">
        <div className="rounded-2xl bg-white/70 p-3 shadow-sm ring-1 ring-[#00000010] backdrop-blur-sm dark:bg-[#1f1e1b]/80 dark:ring-[#6c6a6040]">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#1a1a18] text-sm font-semibold text-white dark:bg-[#eee] dark:text-[#2b2a27]">
              {initial}
            </div>

            <div className="min-w-0 flex-1">
              <p className="truncate font-ui text-sm font-semibold text-[#1a1a18] dark:text-[#eee]">{name}</p>
              <p className="truncate font-ui text-xs text-[#6b6a68] dark:text-[#9a9893]">{secondary}</p>
            </div>
          </div>

          <div className="mt-3 flex items-center justify-between gap-3 border-[#00000010] border-t pt-3 dark:border-[#6c6a6040]">
            <Link
              href="/admin"
              className="font-ui text-sm text-[#1a1a18] underline decoration-[#00000025] underline-offset-4 hover:decoration-[#00000055] dark:text-[#eee] dark:decoration-[#ffffff30] dark:hover:decoration-[#ffffff60]"
            >
              Admin
            </Link>
            <button
              type="button"
              onClick={clearAuth}
              className="font-ui text-sm text-[#6b6a68] underline decoration-[#00000025] underline-offset-4 hover:text-[#1a1a18] hover:decoration-[#00000055] dark:text-[#9a9893] dark:decoration-[#ffffff30] dark:hover:text-[#eee] dark:hover:decoration-[#ffffff60]"
            >
              Logout
            </button>
          </div>
        </div>
      </div>
    </ThreadListPrimitive.Root>
  );
}
