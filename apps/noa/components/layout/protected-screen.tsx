"use client";

import type { ReactNode } from "react";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { getAuthUser } from "@/components/lib/auth/auth-storage";
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
  const ready = useRequireAuth();
  const user = getAuthUser();
  const isAdmin = user?.roles?.includes("admin") ?? false;

  useEffect(() => {
    if (!ready || !requireAdmin || isAdmin) {
      return;
    }

    router.replace("/assistant");
  }, [isAdmin, ready, requireAdmin, router]);

  if (!ready || (requireAdmin && !isAdmin)) {
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
