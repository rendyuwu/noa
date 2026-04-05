import { RolesAdminPage } from "@/components/admin/roles-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function AdminRolesPage() {
  return (
    <AdminProtectedScreen title="Roles" description="Define roles and control per-role tool access.">
      <RolesAdminPage />
    </AdminProtectedScreen>
  );
}
