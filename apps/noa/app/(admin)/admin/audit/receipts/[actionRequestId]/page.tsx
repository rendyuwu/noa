import { PlaceholderAdminRoute } from "@/components/admin/placeholder-admin-route";

export default async function AdminAuditReceiptPage({
  params,
}: {
  params: Promise<{ actionRequestId: string }>;
}) {
  const { actionRequestId } = await params;

  return (
    <PlaceholderAdminRoute
      eyebrow="Receipt"
      title={`Receipt ${actionRequestId}`}
      description="Receipt detail route scaffolded inside the shared admin shell."
      placeholderDescription="Receipt fetching/rendering parity is still pending, but the dedicated route contract now exists in apps/noa."
    />
  );
}
