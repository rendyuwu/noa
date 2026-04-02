"use client";

import type { ReactNode } from "react";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { clearAuth } from "@/components/lib/auth/auth-storage";
import { useRequireAuth } from "@/components/lib/auth/use-require-auth";

import { AppShell } from "./app-shell";

type ProtectedScreenProps = {
  children: ReactNode;
  title: string;
  description: string;
  requireAdmin?: boolean;
};

export function ProtectedScreen({
  children,
  title,
  description,
  requireAdmin = false,
}: ProtectedScreenProps) {
  const router = useRouter();
  const { error, ready, refresh, user, validating } = useRequireAuth();
  const isAdmin = user?.roles?.includes("admin") ?? false;

  useEffect(() => {
    if (!ready || !requireAdmin || isAdmin) {
      return;
    }

    router.replace("/assistant");
  }, [isAdmin, ready, requireAdmin, router]);

  if (error) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="w-full max-w-md rounded-2xl border border-border bg-surface px-5 py-5 shadow-soft">
          <h1 className="text-lg font-semibold text-text">Session validation failed</h1>
          <p className="mt-2 font-ui text-sm text-muted">{error}</p>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row">
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-xl bg-accent px-4 py-2.5 font-ui text-sm font-semibold text-accent-foreground"
              onClick={() => void refresh()}
            >
              Retry validation
            </button>
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-xl border border-border bg-bg px-4 py-2.5 font-ui text-sm font-medium text-text"
              onClick={() => clearAuth({ returnTo: "/assistant", redirect: true })}
            >
              Sign out
            </button>
          </div>
        </div>
      </main>
    );
  }

  if (!ready || validating || (requireAdmin && !isAdmin)) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="rounded-2xl border border-border bg-surface px-5 py-4 text-sm text-muted shadow-soft">
          Loading your workspace…
        </div>
      </main>
    );
  }

  return (
    <AppShell title={title} description={description} user={user}>
      {children}
    </AppShell>
  );
}
