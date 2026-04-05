import { WhmServersAdminPage } from "@/components/admin/whm-servers-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function AdminWhmServersPage() {
  return (
    <AdminProtectedScreen
      title="WHM servers"
      description="Manage WHM connection profiles and validate access before use."
    >
      <WhmServersAdminPage />
    </AdminProtectedScreen>
  );
}
