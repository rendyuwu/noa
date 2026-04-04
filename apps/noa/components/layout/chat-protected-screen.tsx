"use client";

import type { ReactNode } from "react";

import { clearAuth } from "@/components/lib/auth/auth-storage";
import { useRequireAuth } from "@/components/lib/auth/use-require-auth";
import { Button } from "@/components/ui/button";

import { ChatShell } from "./chat-shell";

type ChatProtectedScreenProps = {
  children: ReactNode;
};

export function ChatProtectedScreen({ children }: ChatProtectedScreenProps) {
  const { error, ready, refresh, user, validating } = useRequireAuth();

  if (error) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="w-full max-w-md rounded-2xl border border-border bg-surface px-5 py-5 shadow-soft">
          <h1 className="text-lg font-semibold text-text">Session validation failed</h1>
          <p className="mt-2 font-ui text-sm text-muted">{error}</p>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row">
            <Button className="rounded-xl font-ui text-sm font-semibold" onClick={() => void refresh()}>
              Retry validation
            </Button>
            <Button
              variant="outline"
              className="rounded-xl font-ui text-sm font-medium"
              onClick={() => clearAuth({ returnTo: "/assistant", redirect: true })}
            >
              Sign out
            </Button>
          </div>
        </div>
      </main>
    );
  }

  if (!ready || validating) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="rounded-2xl border border-border bg-surface px-5 py-4 text-sm text-muted shadow-soft">
          Loading…
        </div>
      </main>
    );
  }

  return <ChatShell user={user}>{children}</ChatShell>;
}
