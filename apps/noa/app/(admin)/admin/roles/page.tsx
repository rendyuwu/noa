import { RolesAdminPage } from "@/components/admin/roles-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function AdminRolesPage() {
  return (
    <AdminProtectedScreen title="Roles" description="Shared shell route for role and allowlist management parity.">
      <RolesAdminPage />
    </AdminProtectedScreen>
  );
}
