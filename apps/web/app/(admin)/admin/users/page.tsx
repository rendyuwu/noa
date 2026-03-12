"use client";

import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { useRequireAuth } from "@/components/lib/auth-store";

export default function AdminUsersPage() {
  const ready = useRequireAuth();

  if (!ready) {
    return null;
  }

  return <UsersAdminPage />;
}
