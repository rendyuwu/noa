"use client";

import { AdminSidebarShell } from "@/components/admin/admin-sidebar-shell";
import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { useRequireAuth } from "@/components/lib/auth-store";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime-provider";

export default function AdminUsersPage() {
  const ready = useRequireAuth();

  if (!ready) {
    return null;
  }

  return (
    <div className="min-h-dvh bg-bg p-0">
      <NoaAssistantRuntimeProvider>
        <AdminSidebarShell>
          <UsersAdminPage />
        </AdminSidebarShell>
      </NoaAssistantRuntimeProvider>
    </div>
  );
}
