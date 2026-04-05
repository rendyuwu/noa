import { ProxmoxServersAdminPage } from "@/components/admin/proxmox-servers-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function AdminProxmoxServersPage() {
  return (
    <AdminProtectedScreen
      title="Proxmox servers"
      description="Manage Proxmox connection profiles and validate access before use."
    >
      <ProxmoxServersAdminPage />
    </AdminProtectedScreen>
  );
}
