import { ProxmoxServersAdminPage } from "@/components/admin/proxmox-servers-admin-page";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AdminProxmoxServersPage() {
  return (
    <ProtectedScreen
      requireAdmin
      title="Proxmox servers"
      description="Create, update, validate, and remove Proxmox server connection profiles."
    >
      <ProxmoxServersAdminPage />
    </ProtectedScreen>
  );
}
