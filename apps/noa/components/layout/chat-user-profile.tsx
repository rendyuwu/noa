"use client";

import { LogOut, Settings } from "lucide-react";
import Link from "next/link";

import type { AuthUser } from "@/components/lib/auth/types";
import { clearAuth } from "@/components/lib/auth/auth-storage";
import { Badge } from "@/components/ui/badge";

type ChatUserProfileProps = {
  user: AuthUser | null;
  collapsed: boolean;
  isAdmin: boolean;
};

function UserAvatar({ name, className }: { name: string; className?: string }) {
  const initial = name.charAt(0).toUpperCase();
  return (
    <div
      className={[
        "flex shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground",
        className ?? "size-8",
      ].join(" ")}
      aria-hidden="true"
    >
      {initial}
    </div>
  );
}

export function ChatUserProfile({ user, collapsed, isAdmin }: ChatUserProfileProps) {
  const displayName = user?.display_name || user?.email || "User";
  const email = user?.email ?? "";

  return (
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
      {isAdmin && collapsed && (
        <Link
          href="/admin"
          className="flex items-center justify-center rounded-lg p-2 text-muted transition hover:bg-surface-2 hover:text-text"
          aria-label="Admin"
        >
          <Settings className="size-4" />
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
        <div className="mt-1 flex items-center gap-2.5 rounded-lg px-3 py-2">
          <UserAvatar name={displayName} />
          <div className="min-w-0 flex-1">
            <p className="truncate font-ui text-sm font-medium text-text">{displayName}</p>
            {email && email !== displayName && (
              <p className="truncate font-ui text-xs text-muted">{email}</p>
            )}
          </div>
          {isAdmin && (
            <Badge variant="muted" className="shrink-0 text-[10px]">Admin</Badge>
          )}
        </div>
      )}

      {collapsed && user && (
        <div className="mt-1 flex justify-center">
          <UserAvatar name={displayName} className="size-8" />
        </div>
      )}
    </div>
  );
}
