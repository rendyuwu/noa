import { AdminPlaceholder } from "@/components/admin/admin-placeholder";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminRolesPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="Roles"
      description="Shared shell route for role and allowlist management parity."
    >
      <AdminPlaceholder
        eyebrow="Admin"
        title="Roles management scaffold"
        description="The new route composition is in place without duplicating shell/runtime logic per page. Feature-level forms and mutations are still pending."
      />
    </ProtectedScreen>
  );
}
