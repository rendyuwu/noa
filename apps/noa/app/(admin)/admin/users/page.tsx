import { AdminPlaceholder } from "@/components/admin/admin-placeholder";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminUsersPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="Users"
      description="Unified admin shell target for user management parity."
    >
      <AdminPlaceholder
        eyebrow="Admin"
        title="Users management scaffold"
        description="This route is now inside the shared shell and auth boundary. CRUD parity and contract tests still need to be ported from the brownfield app."
      />
    </ProtectedScreen>
  );
}
