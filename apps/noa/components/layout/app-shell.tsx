"use client";

import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";

import { clearAuth } from "@/components/lib/auth/auth-storage";
import type { AuthUser } from "@/components/lib/auth/types";

import { navItems } from "./nav-items";

const COLLAPSED_KEY = "noa.shell.collapsed";

type AppShellProps = {
  children: ReactNode;
  title: string;
  description: string;
  user: AuthUser | null;
};

export function AppShell({ children, title, description, user }: AppShellProps) {
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

  const items = useMemo(
    () => navItems.filter((item) => (item.adminOnly ? isAdmin : true)),
    [isAdmin],
  );

  const Sidebar = (
    <aside className="flex h-full flex-col gap-5 border-r border-border/80 bg-surface px-3 py-4 text-sm shadow-soft">
      <div className="flex items-center justify-between gap-3 px-2">
        <div className={collapsed ? "sr-only" : "min-w-0"}>
          <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">NOA</p>
          <p className="truncate text-lg font-semibold text-text">Browser rewrite</p>
        </div>
        <button
          type="button"
          className="hidden rounded-lg border border-border bg-bg/70 p-2 text-muted transition hover:bg-surface-2 md:inline-flex"
          onClick={() => setCollapsed((value) => !value)}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </button>
        <button
          type="button"
          className="rounded-lg border border-border bg-bg/70 p-2 text-muted transition hover:bg-surface-2 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-label="Close navigation"
        >
          <X className="size-4" />
        </button>
      </div>

      <nav className="flex flex-1 flex-col gap-1">
        {items.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const isLogout = item.href === "/login";

          const content = (
            <>
              <Icon className="size-4 shrink-0" />
              {!collapsed && <span className="truncate">{item.label}</span>}
              {isLogout && !collapsed ? <LogOut className="ml-auto size-4 shrink-0 opacity-70" /> : null}
            </>
          );

          const className = [
            "flex items-center gap-3 rounded-xl px-3 py-2.5 font-ui transition",
            active ? "bg-accent text-accent-foreground" : "text-muted hover:bg-surface-2 hover:text-text",
          ].join(" ");

          if (isLogout) {
            return (
              <button
                key={item.href}
                type="button"
                className={className}
                onClick={() => clearAuth({ returnTo: "/assistant", redirect: true })}
              >
                {content}
              </button>
            );
          }

          return (
            <Link key={item.href} href={item.href} className={className}>
              {content}
            </Link>
          );
        })}
      </nav>

      {!collapsed && (
        <div className="rounded-xl border border-border/70 bg-bg/70 px-3 py-3 font-ui text-xs text-muted">
          <p className="font-medium text-text">Signed in as</p>
          <p className="mt-1 truncate">{user?.display_name || user?.email || "Unknown user"}</p>
        </div>
      )}
    </aside>
  );

  return (
    <div className="min-h-dvh bg-bg text-text">
      <div className="flex min-h-dvh">
        <div className={`${collapsed ? "hidden md:block md:w-[88px]" : "hidden md:block md:w-[288px]"}`}>{Sidebar}</div>

        {mobileOpen ? (
          <div className="fixed inset-0 z-40 flex md:hidden">
            <div className="w-[290px] max-w-[85vw]">{Sidebar}</div>
            <button
              type="button"
              className="flex-1 bg-black/30"
              aria-label="Dismiss navigation overlay"
              onClick={() => setMobileOpen(false)}
            />
          </div>
        ) : null}

        <div className="flex min-h-dvh flex-1 flex-col">
          <header className="sticky top-0 z-20 border-b border-border/70 bg-bg/90 px-4 py-3 backdrop-blur sm:px-6">
            <div className="flex items-start gap-3">
              <button
                type="button"
                className="inline-flex rounded-lg border border-border bg-surface p-2 text-muted transition hover:bg-surface-2 md:hidden"
                onClick={() => setMobileOpen(true)}
                aria-label="Open navigation"
              >
                <Menu className="size-4" />
              </button>
              <div className="min-w-0 flex-1">
                <h1 className="text-xl font-semibold tracking-[-0.02em] sm:text-2xl">{title}</h1>
                <p className="mt-1 max-w-3xl font-ui text-sm text-muted">{description}</p>
              </div>
            </div>
          </header>
          <main className="flex-1 px-4 py-4 sm:px-6 sm:py-6">{children}</main>
        </div>
      </div>
    </div>
  );
}
