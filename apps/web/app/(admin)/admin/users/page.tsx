"use client";

import { AdminShell } from "@/components/admin/admin-shell";
import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { useVerifiedAuth } from "@/components/lib/use-verified-auth";

export default function AdminUsersPage() {
  const { ready } = useVerifiedAuth({ requireAdmin: true });

  if (!ready) {
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
