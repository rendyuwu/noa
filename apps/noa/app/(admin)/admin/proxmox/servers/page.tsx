import { AdminPlaceholder } from "@/components/admin/admin-placeholder";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminProxmoxServersPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="Proxmox servers"
      description="Shared shell route for Proxmox server CRUD and validation parity."
    >
      <AdminPlaceholder
        eyebrow="Infrastructure"
        title="Proxmox server scaffold"
        description="Route composition is established; contract-aware forms, tables, and validation flows still need to be added."
      />
    </ProtectedScreen>
  );
}
