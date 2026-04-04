"use client";

import { type ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { clearAuth } from "@/components/lib/auth/auth-storage";
import type { AuthUser } from "@/components/lib/auth/types";

import { adminNavItems, backToChatAction } from "./admin-nav-items";

const COLLAPSED_KEY = "noa.admin-shell.collapsed";

type AdminShellProps = {
  children: ReactNode;
  title: string;
  description: string;
  user: AuthUser | null;
};

export function AdminShell({ children, title, description, user }: AdminShellProps) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

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

  const NavLink = ({ href, label, icon: Icon, active }: { href: string; label: string; icon: React.ComponentType<{ className?: string }>; active: boolean }) => {
    const className = [
      "flex items-center gap-3 rounded-xl px-3 py-2.5 font-ui text-sm transition",
      active ? "bg-surface-2 text-text border-l-2 border-accent" : "text-muted hover:bg-surface-2 hover:text-text",
    ].join(" ");

    const content = (
      <>
        <Icon className="size-4 shrink-0" />
        {!collapsed && <span className="truncate">{label}</span>}
      </>
    );

    return collapsed ? (
      <Tooltip>
        <TooltipTrigger asChild>
          <Link href={href} className={className}>{content}</Link>
        </TooltipTrigger>
        <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    ) : (
      <Link href={href} className={className}>{content}</Link>
    );
  };

  const Sidebar = (
    <aside className="flex h-full flex-col gap-5 border-r border-border/80 bg-surface px-3 py-4 text-sm shadow-soft">
      <div className="flex items-center justify-between gap-3 px-2">
        {!collapsed && (
          <div className="min-w-0">
            <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">NOA</p>
            <p className="truncate text-lg font-semibold text-text">Admin</p>
          </div>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="hidden rounded-lg border border-border bg-bg/70 text-muted hover:bg-surface-2 md:inline-flex"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="rounded-lg border border-border bg-bg/70 text-muted hover:bg-surface-2 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-label="Close navigation"
        >
          <X className="size-4" />
        </Button>
      </div>

      <nav className="flex flex-1 flex-col gap-1">
        <NavLink
          href={backToChatAction.href}
          label={backToChatAction.label}
          icon={backToChatAction.icon}
          active={false}
        />
        <div className="my-2 border-t border-border/60" />
        {adminNavItems.map((item) => (
          <NavLink
            key={item.href}
            href={item.href}
            label={item.label}
            icon={item.icon}
            active={pathname === item.href || pathname.startsWith(`${item.href}/`)}
          />
        ))}
      </nav>

      <div className="space-y-1">
        <button
          type="button"
          onClick={() => clearAuth({ returnTo: "/assistant", redirect: true })}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 font-ui text-sm text-muted transition hover:bg-surface-2 hover:text-text"
        >
          <LogOut className="size-4 shrink-0" />
          {!collapsed && <span>Sign out</span>}
        </button>
        {!collapsed && user && (
          <div className="rounded-xl border border-border/70 bg-bg/70 px-3 py-3 font-ui text-xs text-muted">
            <p className="font-medium text-text">Signed in as</p>
            <p className="mt-1 truncate">{user.display_name || user.email || "Unknown user"}</p>
          </div>
        )}
      </div>
    </aside>
  );

  return (
    <TooltipProvider delayDuration={200}>
      <div className="min-h-dvh bg-bg text-text">
        <div className="flex min-h-dvh">
          <div className={collapsed ? "hidden md:block md:w-[88px]" : "hidden md:block md:w-[288px]"}>
            {Sidebar}
          </div>

          {mobileOpen && (
            <div className="fixed inset-0 z-40 flex md:hidden">
              <div className="w-[290px] max-w-[85vw]">{Sidebar}</div>
              <button
                type="button"
                className="flex-1 bg-overlay/40"
                aria-label="Dismiss navigation overlay"
                onClick={() => setMobileOpen(false)}
              />
            </div>
          )}

          <div className="flex min-h-dvh flex-1 flex-col">
            <header className="sticky top-0 z-20 border-b border-border/70 bg-bg/90 px-4 py-3 backdrop-blur sm:px-6">
              <div className="flex items-start gap-3">
                <Button
                  variant="ghost"
                  size="icon"
                  className="rounded-lg border border-border bg-surface text-muted hover:bg-surface-2 md:hidden"
                  onClick={() => setMobileOpen(true)}
                  aria-label="Open navigation"
                >
                  <Menu className="size-4" />
                </Button>
                <div className="min-w-0 flex-1">
                  <h1 className="text-xl font-semibold tracking-[-0.02em] sm:text-2xl">{title}</h1>
                  <p className="mt-1 max-w-3xl font-ui text-sm text-muted">{description}</p>
                </div>
                <ThemeToggle className="shrink-0" />
              </div>
            </header>
            <main className="flex-1 px-4 py-4 sm:px-6 sm:py-6">{children}</main>
          </div>
        </div>
      </div>
    </TooltipProvider>
  );
}
