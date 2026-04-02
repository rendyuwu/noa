import { AdminPlaceholder } from "@/components/admin/admin-placeholder";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminAuditPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="Audit"
      description="Shared shell route for audit list, filters, and receipt navigation."
    >
      <AdminPlaceholder
        eyebrow="Admin"
        title="Audit log scaffold"
        description="The receipt route and list page now have dedicated destinations inside the shared admin shell, ready for cursor/filter parity work."
      />
    </ProtectedScreen>
  );
}
