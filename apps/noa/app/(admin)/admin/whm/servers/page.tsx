import { WhmServersAdminPage } from "@/components/admin/whm-servers-admin-page";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminWhmServersPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="WHM servers"
      description="Create, update, validate, and remove WHM server connection profiles."
    >
      <WhmServersAdminPage />
    </ProtectedScreen>
  );
}
