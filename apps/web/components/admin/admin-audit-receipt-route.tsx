"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { AdminSidebarShell } from "@/components/admin/admin-sidebar-shell";
import { AuditReceiptPage } from "@/components/admin/audit-receipt-page";
import { getAuthUser, useRequireAuth } from "@/components/lib/auth-store";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime-provider";

export function AdminAuditReceiptRouteClient({
  actionRequestId,
}: {
  actionRequestId: string;
}) {
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
      <NoaAssistantRuntimeProvider>
        <AdminSidebarShell>
          <AuditReceiptPage actionRequestId={actionRequestId} />
        </AdminSidebarShell>
      </NoaAssistantRuntimeProvider>
    </div>
  );
}
