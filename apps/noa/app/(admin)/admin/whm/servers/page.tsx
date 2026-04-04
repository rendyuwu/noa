import { WhmServersAdminPage } from "@/components/admin/whm-servers-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function AdminWhmServersPage() {
  return (
    <AdminProtectedScreen title="WHM servers" description="Create, update, validate, and remove WHM server connection profiles.">
      <WhmServersAdminPage />
    </AdminProtectedScreen>
  );
}
