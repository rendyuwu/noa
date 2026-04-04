import { UsersAdminPage } from "@/components/admin/users-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function UsersPage() {
  return (
    <AdminProtectedScreen title="Users" description="Unified admin shell target for user management parity.">
      <UsersAdminPage />
    </AdminProtectedScreen>
  );
}
