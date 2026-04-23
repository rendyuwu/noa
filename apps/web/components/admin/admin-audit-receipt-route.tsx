"use client";

import { AdminShell } from "@/components/admin/admin-shell";
import { AuditReceiptPage } from "@/components/admin/audit-receipt-page";
import { useVerifiedAuth } from "@/components/lib/use-verified-auth";

export function AdminAuditReceiptRouteClient({
  actionRequestId,
}: {
  actionRequestId: string;
}) {
  const { ready } = useVerifiedAuth({ requireAdmin: true });

  if (!ready) {
    return null;
  }

  return (
    <div className="min-h-dvh bg-background p-0">
      <AdminShell>
          <AuditReceiptPage actionRequestId={actionRequestId} />
        </AdminShell>
    </div>
  );
}
