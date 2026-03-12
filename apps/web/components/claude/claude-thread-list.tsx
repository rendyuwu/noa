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
  LayersIcon,
  MagnifyingGlassIcon,
  PersonIcon,
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
      className="flex w-full items-center justify-start gap-3 rounded-lg px-4 py-2 font-ui text-sm text-muted opacity-70 transition-colors hover:bg-surface-2/60 hover:text-text"
    >
      <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
        {icon}
      </span>
      {label}
    </button>
  );
}

function NavLinkItem({
  icon,
  label,
  href,
}: {
  icon: ReactNode;
  label: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="flex w-full items-center justify-start gap-3 rounded-lg px-4 py-2 font-ui text-sm text-muted transition-colors hover:bg-surface-2/60 hover:text-text active:scale-[0.99]"
    >
      <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
        {icon}
      </span>
      {label}
    </Link>
  );
}

const ThreadListItem: FC<{ onSelect?: () => void }> = ({ onSelect }) => {
  return (
    <ThreadListItemPrimitive.Root className="group flex items-center gap-2 rounded-lg px-4 py-2 transition-colors hover:bg-surface-2/60 data-[active]:bg-surface-2/60">
      <ThreadListItemPrimitive.Trigger
        onClick={onSelect}
        className="min-w-0 flex-1 rounded-md text-left font-ui text-sm text-text outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
      >
        <span className="block truncate">
          <ThreadListItemPrimitive.Title fallback="Untitled" />
        </span>
      </ThreadListItemPrimitive.Trigger>

      <ThreadListItemPrimitive.Delete
        className="flex h-7 w-7 items-center justify-center rounded-md text-muted opacity-0 transition hover:bg-surface-2/60 hover:text-text group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
        aria-label="Delete thread"
      >
        <TrashIcon width={16} height={16} />
      </ThreadListItemPrimitive.Delete>
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
  const isAdmin = user?.roles?.includes("admin") ?? false;

  return (
    <ThreadListPrimitive.Root className="flex h-full flex-col bg-bg">
      <div className="pt-3 font-ui">
        <div className="flex items-center justify-between px-4">
          <div className="font-serif text-lg font-semibold tracking-[-0.01em] text-text">
            NOA
          </div>

          {onCloseSidebar ? (
            <button
              type="button"
              onClick={onCloseSidebar}
              aria-label="Close sidebar"
              className="flex h-9 w-9 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-2/60 hover:text-text active:scale-[0.98]"
            >
              <ColumnsIcon width={18} height={18} />
            </button>
          ) : (
            <div aria-hidden="true" className="h-9 w-9" />
          )}
        </div>

        <div className="mt-3">
          <ThreadListPrimitive.New
            onClick={onSelectThread}
            className="flex w-full items-center gap-3 rounded-lg px-4 py-2 font-ui text-sm text-text transition-colors hover:bg-surface-2/60 active:scale-[0.99]"
          >
            <span
              aria-hidden="true"
              className="flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface text-muted"
            >
              <PlusIcon width={14} height={14} />
            </span>
            New chat
          </ThreadListPrimitive.New>

          <div className="mt-2">
            <DisabledNavItem icon={<MagnifyingGlassIcon width={16} height={16} />} label="Search" />
            {isAdmin ? (
              <NavLinkItem icon={<PersonIcon width={16} height={16} />} label="Users" href="/admin/users" />
            ) : null}
            <DisabledNavItem icon={<LayersIcon width={16} height={16} />} label="Projects" />
            <DisabledNavItem icon={<ArchiveIcon width={16} height={16} />} label="Artifacts" />
            <DisabledNavItem icon={<CodeIcon width={16} height={16} />} label="Code" />
          </div>
        </div>
      </div>

      <div className="mt-4 flex min-h-0 flex-1 flex-col font-ui">
        <p className="px-4 pb-2 text-xs font-medium uppercase tracking-[0.12em] text-muted">Recents</p>
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

      <div className="border-border border-t px-4 py-3 font-ui">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-text text-sm font-semibold text-bg">
            {initial}
          </div>

          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-text">{name}</p>
            <p className="truncate text-xs text-muted">{secondary}</p>
          </div>
        </div>

        <div className="mt-2 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={clearAuth}
            className="text-sm text-muted underline decoration-border/60 underline-offset-4 hover:text-text hover:decoration-border"
          >
            Logout
          </button>
        </div>
      </div>
    </ThreadListPrimitive.Root>
  );
}
