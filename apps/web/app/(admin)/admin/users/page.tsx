"use client";

import { useRequireAuth } from "@/components/lib/auth-store";

export default function AdminUsersPage() {
  const ready = useRequireAuth();

  if (!ready) {
    return null;
  }

  return (
    <main className="min-h-dvh bg-bg p-6">
      <h1 className="text-2xl font-semibold">Users</h1>
      <p className="mt-2 text-sm opacity-80">
        Admin user management UI will live here.
      </p>
    </main>
  );
}
