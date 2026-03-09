"use client";

import Link from "next/link";
import type { FC } from "react";

import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
} from "@assistant-ui/react";
import { PlusIcon, TrashIcon } from "@radix-ui/react-icons";

import { clearAuth } from "@/components/lib/auth-store";

const ThreadListItem: FC<{ onSelect?: () => void }> = ({ onSelect }) => {
  return (
    <ThreadListItemPrimitive.Root className="group px-2">
      <div className="flex items-center gap-2 rounded-lg px-2 py-2 transition hover:bg-[#ffffff80] dark:hover:bg-[#1f1e1b]/60">
        <ThreadListItemPrimitive.Trigger
          onClick={onSelect}
          className="min-w-0 flex-1 text-left text-sm text-[#1a1a18] outline-none dark:text-[#eee]"
        >
          <span className="block truncate">
            <ThreadListItemPrimitive.Title fallback="Untitled" />
          </span>
        </ThreadListItemPrimitive.Trigger>

        <ThreadListItemPrimitive.Delete
          className="flex h-7 w-7 items-center justify-center rounded-md text-[#6b6a68] opacity-0 transition hover:bg-[#f5f5f0] hover:text-[#1a1a18] group-hover:opacity-100 dark:text-[#9a9893] dark:hover:bg-[#393937] dark:hover:text-[#eee]"
          aria-label="Delete thread"
        >
          <TrashIcon width={16} height={16} />
        </ThreadListItemPrimitive.Delete>
      </div>
    </ThreadListItemPrimitive.Root>
  );
};

export function ClaudeThreadList({ onSelectThread }: { onSelectThread?: () => void }) {
  return (
    <ThreadListPrimitive.Root className="flex h-full flex-col bg-[#F5F5F0] font-serif dark:bg-[#2b2a27]">
      <div className="px-4 pt-4">
        <ThreadListPrimitive.New className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#ae5630] px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#c4633a] active:scale-[0.99]">
          <PlusIcon width={16} height={16} />
          New chat
        </ThreadListPrimitive.New>
      </div>

      <div className="mt-3 flex-1 overflow-y-auto pb-3">
        <ThreadListPrimitive.Items components={{ ThreadListItem: (props: any) => <ThreadListItem {...props} onSelect={onSelectThread} /> }} />
      </div>

      <div className="border-[#00000015] border-t px-4 py-4 dark:border-[#6c6a6040]">
        <div className="flex items-center justify-between gap-3">
          <Link
            href="/admin"
            className="text-sm text-[#1a1a18] underline decoration-[#00000025] underline-offset-4 hover:decoration-[#00000055] dark:text-[#eee] dark:decoration-[#ffffff30] dark:hover:decoration-[#ffffff60]"
          >
            Admin
          </Link>
          <button
            type="button"
            onClick={clearAuth}
            className="text-sm text-[#6b6a68] underline decoration-[#00000025] underline-offset-4 hover:text-[#1a1a18] hover:decoration-[#00000055] dark:text-[#9a9893] dark:decoration-[#ffffff30] dark:hover:text-[#eee] dark:hover:decoration-[#ffffff60]"
          >
            Logout
          </button>
        </div>
      </div>
    </ThreadListPrimitive.Root>
  );
}
