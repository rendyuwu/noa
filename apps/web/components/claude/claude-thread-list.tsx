"use client";

import Link from "next/link";
import type { FC, ReactNode } from "react";

import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
} from "@assistant-ui/react";
import {
  ArchiveIcon,
  ColumnsIcon,
  CodeIcon,
  GearIcon,
  LayersIcon,
  MagnifyingGlassIcon,
  PlusIcon,
  TrashIcon,
} from "@radix-ui/react-icons";

import { formatClaudeGreetingName } from "@/components/claude/claude-greeting";
import { clearAuth, getAuthUser } from "@/components/lib/auth-store";

function DisabledNavItem({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <button
      type="button"
      aria-disabled="true"
      title="Coming soon"
      onClick={(event) => event.preventDefault()}
      className="flex w-full items-center justify-start gap-3 rounded-lg px-3 py-2 font-ui text-sm text-[#6b6a68] opacity-70 transition hover:bg-[#ffffff80] hover:text-[#1a1a18] dark:text-[#9a9893] dark:hover:bg-[#1f1e1b]/60 dark:hover:text-[#eee]"
    >
      <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
        {icon}
      </span>
      {label}
    </button>
  );
}

const ThreadListItem: FC<{ onSelect?: () => void }> = ({ onSelect }) => {
  return (
    <ThreadListItemPrimitive.Root className="group">
      <div className="flex items-center gap-2 rounded-lg px-2 py-2 transition hover:bg-[#ffffff80] dark:hover:bg-[#1f1e1b]/60">
        <ThreadListItemPrimitive.Trigger
          onClick={onSelect}
          className="min-w-0 flex-1 rounded-md text-left font-ui text-sm text-[#1a1a18] outline-none focus-visible:ring-2 focus-visible:ring-[#ae5630]/35 focus-visible:ring-offset-2 focus-visible:ring-offset-[#F5F5F0] dark:text-[#eee] dark:focus-visible:ring-offset-[#2b2a27]"
        >
          <span className="block truncate">
            <ThreadListItemPrimitive.Title fallback="Untitled" />
          </span>
        </ThreadListItemPrimitive.Trigger>

        <ThreadListItemPrimitive.Delete
          className="flex h-7 w-7 items-center justify-center rounded-md text-[#6b6a68] opacity-0 transition hover:bg-[#ffffff80] hover:text-[#1a1a18] group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#ae5630]/35 focus-visible:ring-offset-2 focus-visible:ring-offset-[#F5F5F0] dark:text-[#9a9893] dark:hover:bg-[#1f1e1b]/60 dark:hover:text-[#eee] dark:focus-visible:ring-offset-[#2b2a27]"
          aria-label="Delete thread"
        >
          <TrashIcon width={16} height={16} />
        </ThreadListItemPrimitive.Delete>
      </div>
    </ThreadListItemPrimitive.Root>
  );
};

export function ClaudeThreadList({
  onSelectThread,
  onCloseSidebar,
}: {
  onSelectThread?: () => void;
  onCloseSidebar?: () => void;
}) {
  const user = getAuthUser();
  const name = user ? formatClaudeGreetingName(user) : "NOA User";
  const initial = name.trim().slice(0, 1).toUpperCase() || "U";
  const secondary = user?.email?.trim() || user?.roles?.join(", ") || "Signed in";

  return (
    <ThreadListPrimitive.Root className="flex h-full flex-col bg-[#F5F5F0] dark:bg-[#2b2a27]">
      <div className="px-3 pt-3 font-ui">
        <div className="flex items-center justify-between px-1">
          <div className="font-serif text-lg font-semibold tracking-[-0.01em] text-[#1a1a18] dark:text-[#eee]">
            NOA
          </div>

          {onCloseSidebar ? (
            <button
              type="button"
              onClick={onCloseSidebar}
              aria-label="Close sidebar"
              className="flex h-9 w-9 items-center justify-center rounded-lg text-[#6b6a68] transition hover:bg-[#ffffff80] hover:text-[#1a1a18] active:scale-[0.98] dark:text-[#9a9893] dark:hover:bg-[#1f1e1b]/60 dark:hover:text-[#eee]"
            >
              <ColumnsIcon width={18} height={18} />
            </button>
          ) : (
            <div aria-hidden="true" className="h-9 w-9" />
          )}
        </div>

        <div className="mt-3 px-1">
          <ThreadListPrimitive.New
            onClick={onSelectThread}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2 font-ui text-sm text-[#1a1a18] transition hover:bg-[#ffffff80] active:scale-[0.99] dark:text-[#eee] dark:hover:bg-[#1f1e1b]/60"
          >
            <span
              aria-hidden="true"
              className="flex h-6 w-6 items-center justify-center rounded-full border border-[#00000015] bg-white/60 text-[#6b6a68] dark:border-[#6c6a6040] dark:bg-[#1f1e1b]/60 dark:text-[#9a9893]"
            >
              <PlusIcon width={14} height={14} />
            </span>
            New chat
          </ThreadListPrimitive.New>

          <div className="mt-2">
          <DisabledNavItem icon={<MagnifyingGlassIcon width={16} height={16} />} label="Search" />
          <DisabledNavItem icon={<GearIcon width={16} height={16} />} label="Customize" />
          <DisabledNavItem icon={<LayersIcon width={16} height={16} />} label="Projects" />
          <DisabledNavItem icon={<ArchiveIcon width={16} height={16} />} label="Artifacts" />
          <DisabledNavItem icon={<CodeIcon width={16} height={16} />} label="Code" />
          </div>
        </div>
      </div>

      <div className="mt-4 flex min-h-0 flex-1 flex-col px-3 font-ui">
        <p className="px-3 pb-2 text-xs font-medium uppercase tracking-[0.12em] text-[#8a8985]">Recents</p>
        <div className="min-h-0 flex-1 overflow-y-auto pb-3">
          <ThreadListPrimitive.Items
            components={{
              ThreadListItem: (props: any) => (
                <ThreadListItem {...props} onSelect={onSelectThread} />
              ),
            }}
          />
        </div>
      </div>

      <div className="border-[#00000015] border-t px-3 py-3 font-ui dark:border-[#6c6a6040]">
        <div className="flex items-center gap-3 px-1">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#1a1a18] text-sm font-semibold text-white dark:bg-[#eee] dark:text-[#2b2a27]">
            {initial}
          </div>

          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-[#1a1a18] dark:text-[#eee]">{name}</p>
            <p className="truncate text-xs text-[#6b6a68] dark:text-[#9a9893]">{secondary}</p>
          </div>
        </div>

        <div className="mt-2 flex items-center justify-between gap-3 px-1">
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
