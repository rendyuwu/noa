"use client";

import type { ReactNode } from "react";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { clearAuth } from "@/components/lib/auth/auth-storage";
import { useRequireAuth } from "@/components/lib/auth/use-require-auth";
import { Button } from "@/components/ui/button";

import { AdminShell } from "./admin-shell";

type AdminProtectedScreenProps = {
  children: ReactNode;
  title: string;
  description: string;
};

export function AdminProtectedScreen({ children, title, description }: AdminProtectedScreenProps) {
  const router = useRouter();
  const { error, ready, refresh, user, validating } = useRequireAuth();
  const isAdmin = user?.roles?.includes("admin") ?? false;

  useEffect(() => {
    if (ready && !isAdmin) {
      router.replace("/assistant");
    }
  }, [isAdmin, ready, router]);

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

  if (!ready || validating || !isAdmin) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-10">
        <div className="rounded-2xl border border-border bg-surface px-5 py-4 text-sm text-muted shadow-soft">
          Loading admin panel…
        </div>
      </main>
    );
  }

  return (
    <AdminShell title={title} description={description} user={user}>
      {children}
    </AdminShell>
  );
}
