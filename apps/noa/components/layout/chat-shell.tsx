"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, PanelLeftClose, PanelLeftOpen, Settings, SquarePen, X } from "lucide-react";
import { ThreadListPrimitive, ThreadListItemPrimitive, useAssistantState } from "@assistant-ui/react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { clearAuth } from "@/components/lib/auth/auth-storage";
import type { AuthUser } from "@/components/lib/auth/types";
import { getActiveThreadListItem } from "@/components/lib/runtime/assistant-thread-state";

const COLLAPSED_KEY = "noa.chat-shell.collapsed";

type ChatShellProps = {
  children: ReactNode;
  user: AuthUser | null;
};

function ChatThreadListItem() {
  const activeRemoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const remoteId = useAssistantState(({ threadListItem }) => threadListItem.remoteId ?? null);
  const title = useAssistantState(({ threadListItem }) => threadListItem.title ?? null);
  const isActive = remoteId !== null && activeRemoteId === remoteId;

  return (
    <ThreadListItemPrimitive.Root className="mb-0.5">
      <ThreadListItemPrimitive.Trigger
        className={[
          "block w-full truncate rounded-lg px-3 py-2 text-left font-ui text-sm transition",
          isActive
            ? "bg-surface-2 font-medium text-text"
            : "text-muted hover:bg-surface-2/60 hover:text-text",
        ].join(" ")}
      >
        {title && title.trim() ? title : "Untitled thread"}
      </ThreadListItemPrimitive.Trigger>
    </ThreadListItemPrimitive.Root>
  );
}

export function ChatShell({ children, user }: ChatShellProps) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const isAdmin = user?.roles?.includes("admin") ?? false;

  useEffect(() => {
    const saved = window.localStorage.getItem(COLLAPSED_KEY);
    setCollapsed(saved === "true");
  }, []);

  useEffect(() => {
    window.localStorage.setItem(COLLAPSED_KEY, String(collapsed));
  }, [collapsed]);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const Sidebar = (
    <aside className="flex h-full flex-col bg-surface text-sm">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3">
        {!collapsed && (
          <span className="text-base font-semibold tracking-tight text-text">NOA</span>
        )}
        <div className="flex items-center gap-1">
          <ThreadListPrimitive.New asChild>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 rounded-lg text-muted hover:bg-surface-2 hover:text-text"
                  aria-label="New chat"
                >
                  <SquarePen className="size-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side={collapsed ? "right" : "bottom"}>New chat</TooltipContent>
            </Tooltip>
          </ThreadListPrimitive.New>
          <Button
            variant="ghost"
            size="icon"
            className="hidden size-8 rounded-lg text-muted hover:bg-surface-2 hover:text-text md:inline-flex"
            onClick={() => setCollapsed((v) => !v)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 rounded-lg text-muted hover:bg-surface-2 hover:text-text md:hidden"
            onClick={() => setMobileOpen(false)}
            aria-label="Close sidebar"
          >
            <X className="size-4" />
          </Button>
        </div>
      </div>

      {/* Thread list */}
      {!collapsed && (
        <ScrollArea className="flex-1 px-2">
          <ThreadListPrimitive.Root>
            <ThreadListPrimitive.Items components={{ ThreadListItem: ChatThreadListItem }} />
          </ThreadListPrimitive.Root>
        </ScrollArea>
      )}

      {/* Footer: user + actions */}
      <div className="border-t border-border/60 px-2 py-2">
        {isAdmin && !collapsed && (
          <Link
            href="/admin"
            className="flex items-center gap-2.5 rounded-lg px-3 py-2 font-ui text-sm text-muted transition hover:bg-surface-2 hover:text-text"
          >
            <Settings className="size-4" />
            Admin
          </Link>
        )}
        <button
          type="button"
          onClick={() => clearAuth({ returnTo: "/assistant", redirect: true })}
          className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 font-ui text-sm text-muted transition hover:bg-surface-2 hover:text-text"
        >
          <LogOut className="size-4" />
          {!collapsed && "Sign out"}
        </button>
        {!collapsed && user && (
          <p className="mt-1 truncate px-3 font-ui text-xs text-muted/70">
            {user.display_name || user.email}
          </p>
        )}
      </div>
    </aside>
  );

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex min-h-dvh bg-bg text-text">
        {/* Desktop sidebar */}
        <div className={collapsed ? "hidden md:block md:w-[68px]" : "hidden md:block md:w-[260px]"}>
          {Sidebar}
        </div>

        {/* Mobile sidebar overlay */}
        {mobileOpen && (
          <div className="fixed inset-0 z-40 flex md:hidden">
            <div className="w-[260px] max-w-[85vw]">{Sidebar}</div>
            <button
              type="button"
              className="flex-1 bg-overlay/40"
              aria-label="Dismiss sidebar"
              onClick={() => setMobileOpen(false)}
            />
          </div>
        )}

        {/* Main canvas */}
        <div className="flex min-h-dvh flex-1 flex-col">
          {/* Mobile header */}
          <div className="flex items-center gap-2 px-3 py-2 md:hidden">
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-lg text-muted hover:bg-surface-2"
              onClick={() => setMobileOpen(true)}
              aria-label="Open sidebar"
            >
              <Menu className="size-4" />
            </Button>
            <span className="text-sm font-semibold text-text">NOA</span>
            <ThemeToggle className="ml-auto" />
          </div>

          {/* Content */}
          <main className="flex flex-1 flex-col">{children}</main>
        </div>
      </div>
    </TooltipProvider>
  );
}
