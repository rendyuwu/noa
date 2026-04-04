import { AuditAdminPage } from "@/components/admin/audit-admin-page";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminAuditPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="Audit"
      description="Review action request history, filter events, and open receipt details."
    >
      <AuditAdminPage />
    </ProtectedScreen>
  );
}
