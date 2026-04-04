import { RolesAdminPage } from "@/components/admin/roles-admin-page";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminRolesPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="Roles"
      description="Shared shell route for role and allowlist management parity."
    >
      <RolesAdminPage />
    </ProtectedScreen>
  );
}
