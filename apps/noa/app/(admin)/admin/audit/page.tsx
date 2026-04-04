import { AuditAdminPage } from "@/components/admin/audit-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function AdminAuditPage() {
  return (
    <AdminProtectedScreen title="Audit" description="Review action request history, filter events, and open receipt details.">
      <AuditAdminPage />
    </AdminProtectedScreen>
  );
}
