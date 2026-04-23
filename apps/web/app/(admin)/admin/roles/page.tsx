"use client";

import { AdminShell } from "@/components/admin/admin-shell";
import { RolesAdminPage } from "@/components/admin/roles-admin-page";
import { useVerifiedAuth } from "@/components/lib/use-verified-auth";

export default function AdminRolesPage() {
  const { ready } = useVerifiedAuth({ requireAdmin: true });

  if (!ready) {
    return null;
  }

  return (
    <div className="min-h-dvh bg-background p-0">
      <AdminShell>
          <RolesAdminPage />
        </AdminShell>
    </div>
  );
}
