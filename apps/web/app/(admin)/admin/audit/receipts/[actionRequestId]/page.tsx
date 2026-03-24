import { AdminAuditReceiptRouteClient } from "@/components/admin/admin-audit-receipt-route";

export default async function AdminAuditReceiptRoute({
  params,
}: {
  params: Promise<{ actionRequestId: string }>;
}) {
  const { actionRequestId } = await params;
  return <AdminAuditReceiptRouteClient actionRequestId={actionRequestId} />;
}
