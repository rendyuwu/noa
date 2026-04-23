"use client";

import { AdminShell } from "@/components/admin/admin-shell";
import { ProxmoxServersAdminPage } from "@/components/admin/proxmox-servers-admin-page";
import { useVerifiedAuth } from "@/components/lib/use-verified-auth";

export default function AdminProxmoxServersPage() {
  const { ready } = useVerifiedAuth({ requireAdmin: true });

  if (!ready) {
    return null;
  }

  return (
    <div className="min-h-dvh bg-background p-0">
      <AdminShell>
          <ProxmoxServersAdminPage />
        </AdminShell>
    </div>
  );
}
