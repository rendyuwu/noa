"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { FC, ReactNode } from "react";

import {
  ActivityLogIcon,
  ArrowLeftIcon,
  ColumnsIcon,
  DesktopIcon,
  IdCardIcon,
  PersonIcon,
} from "@radix-ui/react-icons";

import { formatClaudeGreetingName } from "@/components/assistant/claude-greeting";
import { clearAuth, getAuthUser } from "@/components/lib/auth-store";
import { AccountMenu } from "@/components/noa/account-menu";
import { NavLinkItem } from "@/components/noa/nav-link-item";

export type AdminNavSidebarProps = {
  variant?: "expanded" | "collapsed";
  onCollapse?: () => void;
  onExpand?: () => void;
  onClose?: () => void;
};

const adminLinks = [
  { href: "/admin/users", label: "Users", icon: <PersonIcon width={16} height={16} /> },
  { href: "/admin/roles", label: "Roles", icon: <IdCardIcon width={16} height={16} /> },
  { href: "/admin/audit", label: "Audit", icon: <ActivityLogIcon width={16} height={16} /> },
];

const infrastructureLinks = [
  { href: "/admin/whm/servers", label: "WHM Servers", icon: <DesktopIcon width={16} height={16} /> },
  { href: "/admin/proxmox/servers", label: "Proxmox", icon: <DesktopIcon width={16} height={16} /> },
];

const railButtonClassName =
  "flex h-9 w-9 items-center justify-center rounded-xl border border-transparent bg-card/70 text-muted-foreground shadow-sm transition-colors hover:border-border/80 hover:bg-card hover:text-foreground active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-background";

const RailItem: FC<{ label: string; children: ReactNode }> = ({ label, children }) => {
  return (
    <div className="group relative flex">
      {children}
        <div
          aria-hidden="true"
          className={[
            "pointer-events-none absolute top-1/2 left-full z-20 ml-2 -translate-y-1/2",
            "whitespace-nowrap rounded-lg border border-border/80 bg-card/90 px-2 py-1 font-sans text-xs text-foreground shadow-sm backdrop-blur",
            "opacity-0 translate-x-1 transition",
            "group-hover:translate-x-0 group-hover:opacity-100",
            "group-focus-within:translate-x-0 group-focus-within:opacity-100",
        ].join(" ")}
      >
        {label}
      </div>
    </div>
  );
};

function RailNavLink({ href, label, icon }: { href: string; label: string; icon: ReactNode }) {
  const pathname = usePathname();
  const isActive = pathname === href || pathname.startsWith(`${href}/`);

  return (
    <RailItem label={label}>
      <Link
        href={href}
        aria-label={label}
        aria-current={isActive ? "page" : undefined}
        className={[
          railButtonClassName,
          isActive ? "bg-accent text-accent-foreground" : "",
        ].join(" ")}
      >
        <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
          {icon}
        </span>
      </Link>
    </RailItem>
  );
}

export function AdminNavSidebar({
  variant = "expanded",
  onCollapse,
  onExpand,
  onClose,
}: AdminNavSidebarProps) {
  const user = getAuthUser();
  const name = user ? formatClaudeGreetingName(user) : "NOA User";
  const initial = name.trim().slice(0, 1).toUpperCase() || "U";
  const secondary = user?.email?.trim() || "Signed in";

  if (variant === "collapsed") {
    return (
      <nav className="flex h-full flex-col border-r border-sidebar-border/80 bg-sidebar/95 py-3 shadow-[inset_-1px_0_0_rgba(148,163,184,0.12)]">
        <div className="flex flex-1 flex-col items-center gap-1">
          {onExpand ? (
            <RailItem label="Expand sidebar">
              <button
                type="button"
                onClick={onExpand}
                aria-label="Expand sidebar"
                className={railButtonClassName}
              >
                <ColumnsIcon width={14} height={14} />
              </button>
            </RailItem>
          ) : null}

          <RailItem label="Back to Assistant">
            <Link href="/assistant" aria-label="Back to Assistant" className={railButtonClassName}>
              <ArrowLeftIcon width={14} height={14} />
            </Link>
          </RailItem>

            <div className="mt-2 h-px w-6 bg-border/70" />

          <RailNavLink href="/admin/users" label="Users" icon={<PersonIcon width={14} height={14} />} />
          <RailNavLink href="/admin/roles" label="Roles" icon={<IdCardIcon width={14} height={14} />} />
          <RailNavLink href="/admin/audit" label="Audit" icon={<ActivityLogIcon width={14} height={14} />} />

            <div className="mt-2 h-px w-6 bg-border/70" />

          <RailNavLink href="/admin/whm/servers" label="WHM Servers" icon={<DesktopIcon width={14} height={14} />} />
          <RailNavLink href="/admin/proxmox/servers" label="Proxmox" icon={<DesktopIcon width={14} height={14} />} />

          <div className="mt-auto flex flex-col items-center gap-2 pt-3">
            <RailItem label="Account">
              <AccountMenu
                onLogout={clearAuth}
                trigger={
                  <button
                    type="button"
                    aria-label="Account menu"
                    className={railButtonClassName}
                  >
                    <span
                      aria-hidden="true"
                      className="flex h-8 w-8 items-center justify-center rounded-full bg-foreground font-sans text-sm font-semibold text-background"
                    >
                      {initial}
                    </span>
                  </button>
                }
              />
            </RailItem>
          </div>
        </div>
      </nav>
    );
  }

  const closeAction = onCollapse ?? onClose;
  const closeActionLabel = onCollapse ? "Collapse sidebar" : "Close sidebar";

  return (
    <nav className="flex h-full flex-col border-r border-sidebar-border/80 bg-sidebar/95 shadow-[inset_-1px_0_0_rgba(148,163,184,0.12)]">
      <div className="pt-3 font-sans">
        <div className="flex items-center justify-between px-4">
          <span className="font-serif text-lg font-semibold tracking-[-0.02em] text-foreground">NOA</span>

          {closeAction ? (
            <button
              type="button"
              onClick={closeAction}
              aria-label={closeActionLabel}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground active:scale-[0.98]"
            >
              <ColumnsIcon width={18} height={18} />
            </button>
          ) : (
            <div aria-hidden="true" className="h-9 w-9" />
          )}
        </div>

        <div className="mt-3 space-y-1 px-2">
            <Link
              href="/assistant"
              className="flex w-full items-center gap-3 rounded-2xl border border-transparent px-4 py-2.5 text-sm text-muted-foreground transition-colors hover:border-border/70 hover:bg-card/70 hover:text-foreground active:scale-[0.99]"
            >
            <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
              <ArrowLeftIcon width={16} height={16} />
            </span>
            Back to Assistant
          </Link>

          <div className="px-2 pt-3 pb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">
            Admin
          </div>
          {adminLinks.map((item) => (
            <NavLinkItem key={item.href} icon={item.icon} label={item.label} href={item.href} />
          ))}

          <div className="px-2 pt-4 pb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">
            Infrastructure
          </div>
          {infrastructureLinks.map((item) => (
            <NavLinkItem key={item.href} icon={item.icon} label={item.label} href={item.href} />
          ))}
        </div>
      </div>

      <div className="mt-auto border-sidebar-border/80 border-t font-sans">
        <div className="px-4 pb-3">
          <AccountMenu
            onLogout={clearAuth}
            trigger={
              <button
                type="button"
                aria-label="Account menu"
                className="flex w-full items-center gap-3 rounded-2xl border border-transparent px-4 py-3 text-left transition-colors hover:border-border/70 hover:bg-card/70 active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-border/10 bg-foreground text-sm font-semibold text-background shadow-sm">
                  {initial}
                </div>

                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-foreground">{name}</p>
                  <p className="truncate text-xs text-muted-foreground">{secondary}</p>
                </div>
              </button>
            }
          />
        </div>
      </div>
    </nav>
  );
}
