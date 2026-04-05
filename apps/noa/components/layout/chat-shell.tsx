"use client";

import { type ReactNode, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Menu, PanelLeftClose, PanelLeftOpen, SquarePen, X } from "lucide-react";
import { ThreadListPrimitive, useAssistantState } from "@assistant-ui/react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { AuthUser } from "@/components/lib/auth/types";

import { ChatSidebarNav } from "./chat-sidebar-nav";
import { ChatThreadItem } from "./chat-thread-item";
import { ChatUserProfile } from "./chat-user-profile";

const COLLAPSED_KEY = "noa.chat-shell.collapsed";

function ThreadListSkeleton() {
  const hasThreads = useAssistantState(({ threads }) => {
    const items = threads?.threadItems;
    return Array.isArray(items) && items.length > 0;
  });

  if (hasThreads) return null;

  return (
    <div className="space-y-2 px-1 py-1">
      {Array.from({ length: 5 }, (_, i) => `skeleton-${i}`).map((id, i) => (
        <div
          key={id}
          className="h-8 animate-pulse rounded-lg bg-surface-2/60"
          style={{ width: `${75 - i * 8}%` }}
        />
      ))}
    </div>
  );
}

type ChatShellProps = {
  children: ReactNode;
  user: AuthUser | null;
};

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
    if (pathname) {
      setMobileOpen(false);
    }
  }, [pathname]);

  const Sidebar = (
    <aside className="flex h-full min-h-0 flex-col bg-surface text-sm">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3">
        {!collapsed && (
          <span className="font-ui text-base font-semibold tracking-tight text-text">NOA</span>
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

      {/* Sidebar nav (search + feature placeholders) */}
      {!collapsed && <ChatSidebarNav />}

      {/* Divider */}
      {!collapsed && <div className="mx-3 border-t border-border/50" />}

      {/* Thread list with heading */}
      {!collapsed && (
        <section className="flex min-h-0 flex-1 flex-col overflow-hidden" aria-labelledby="chat-recents-heading">
          <p
            id="chat-recents-heading"
            className="px-4 pb-1 pt-3 font-ui text-xs font-medium uppercase tracking-wider text-muted/70"
          >
            Recents
          </p>
          <ScrollArea className="h-full px-2">
            <ThreadListPrimitive.Root>
              <ThreadListSkeleton />
              <ThreadListPrimitive.Items components={{ ThreadListItem: ChatThreadItem }} />
            </ThreadListPrimitive.Root>
          </ScrollArea>
        </section>
      )}

      {/* User profile footer */}
      <div className="mt-auto border-t border-border/60">
        <ChatUserProfile user={user} collapsed={collapsed} isAdmin={isAdmin} />
      </div>
    </aside>
  );

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-dvh overflow-hidden bg-bg text-text">
        {/* Desktop sidebar */}
        <div
          className={[
            "hidden h-full shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out md:block",
            collapsed ? "md:w-[68px]" : "md:w-[260px]",
          ].join(" ")}
        >
          {Sidebar}
        </div>

        {/* Mobile sidebar overlay */}
        {mobileOpen && (
          <div className="fixed inset-0 z-40 flex md:hidden">
            <div className="w-[260px] max-w-[85vw]">{Sidebar}</div>
            <button
              type="button"
              className="flex-1 cursor-pointer bg-overlay/40"
              aria-label="Dismiss sidebar"
              onClick={() => setMobileOpen(false)}
            />
          </div>
        )}

        {/* Main canvas */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
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
            <span className="font-ui text-sm font-semibold text-text">NOA</span>
            <ThemeToggle className="ml-auto" />
          </div>

          {/* Content */}
          <main className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</main>
        </div>
      </div>
    </TooltipProvider>
  );
}
