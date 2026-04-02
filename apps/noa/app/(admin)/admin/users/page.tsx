import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminUsersPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="Users"
      description="Unified admin shell target for user management parity."
    >
      <UsersAdminPage />
    </ProtectedScreen>
  );
}
