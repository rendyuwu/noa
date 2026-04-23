"use client";

import { AdminShell } from "@/components/admin/admin-shell";
import { WhmServersAdminPage } from "@/components/admin/whm-servers-admin-page";
import { useVerifiedAuth } from "@/components/lib/use-verified-auth";

export default function AdminWhmServersPage() {
  const { ready } = useVerifiedAuth({ requireAdmin: true });

  if (!ready) {
    return null;
  }

  return (
    <div className="min-h-dvh bg-background p-0">
      <AdminShell>
          <WhmServersAdminPage />
        </AdminShell>
    </div>
  );
}
