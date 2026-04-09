"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { AdminShell } from "@/components/admin/admin-shell";
import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { getAuthUser, useRequireAuth } from "@/components/lib/auth-store";

export default function AdminUsersPage() {
  const router = useRouter();
  const ready = useRequireAuth();
  const user = getAuthUser();
  const isAdmin = user?.roles?.includes("admin") ?? false;

  useEffect(() => {
    if (!ready) return;
    if (isAdmin) return;
    router.replace("/assistant");
  }, [isAdmin, ready, router]);

  if (!ready || !isAdmin) {
    return null;
  }

  return (
    <div className="min-h-dvh bg-background p-0">
      <AdminShell>
          <UsersAdminPage />
        </AdminShell>
    </div>
  );
}
