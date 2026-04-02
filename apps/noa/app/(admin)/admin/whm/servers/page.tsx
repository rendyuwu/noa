import { AdminPlaceholder } from "@/components/admin/admin-placeholder";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminWhmServersPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="WHM servers"
      description="Shared shell route for WHM server CRUD and validation parity."
    >
      <AdminPlaceholder
        eyebrow="Infrastructure"
        title="WHM server scaffold"
        description="Backend contract wiring and good/bad validation fixtures remain to be implemented in the next lane."
      />
    </ProtectedScreen>
  );
}
