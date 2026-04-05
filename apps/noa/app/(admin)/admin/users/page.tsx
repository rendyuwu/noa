import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function UsersPage() {
  return (
    <AdminProtectedScreen title="Users" description="Manage user activation, roles, and permissions.">
      <UsersAdminPage />
    </AdminProtectedScreen>
  );
}
