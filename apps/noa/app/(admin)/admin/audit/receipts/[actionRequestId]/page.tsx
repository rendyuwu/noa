import { AdminPlaceholder } from "@/components/admin/admin-placeholder";
import { ProtectedScreen } from "@/components/layout/protected-screen";

export default async function AdminAuditReceiptPage({
  params,
}: {
  params: Promise<{ actionRequestId: string }>;
}) {
  const { actionRequestId } = await params;

  return (
    <ProtectedScreen
      requireAdmin
      title="Audit receipt"
      description="Receipt detail route scaffolded inside the shared admin shell."
    >
      <AdminPlaceholder
        eyebrow="Receipt"
        title={`Receipt ${actionRequestId}`}
        description="Receipt fetching/rendering parity is still pending, but the dedicated route contract now exists in apps/noa."
      />
    </ProtectedScreen>
  );
}
