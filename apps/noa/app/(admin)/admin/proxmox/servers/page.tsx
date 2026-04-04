import { ProxmoxServersAdminPage } from "@/components/admin/proxmox-servers-admin-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default function AdminProxmoxServersPage() {
  return (
    <AdminProtectedScreen title="Proxmox servers" description="Create, update, validate, and remove Proxmox server connection profiles.">
      <ProxmoxServersAdminPage />
    </AdminProtectedScreen>
  );
}
