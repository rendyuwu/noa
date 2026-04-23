"use client";

import { AdminShell } from "@/components/admin/admin-shell";
import { AuditAdminPage } from "@/components/admin/audit-admin-page";
import { useVerifiedAuth } from "@/components/lib/use-verified-auth";

export default function AdminAuditPage() {
  const { ready } = useVerifiedAuth({ requireAdmin: true });

  if (!ready) {
    return null;
  }

  return (
    <div className="min-h-dvh bg-background p-0">
      <AdminShell>
          <AuditAdminPage />
        </AdminShell>
    </div>
  );
}
