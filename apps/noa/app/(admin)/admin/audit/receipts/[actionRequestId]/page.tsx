import { AuditReceiptPage } from "@/components/admin/audit-receipt-page";
import { AdminProtectedScreen } from "@/components/layout/admin-protected-screen";

export default async function AdminAuditReceiptPage({
  params,
}: {
  params: Promise<{ actionRequestId: string }>;
}) {
  const { actionRequestId } = await params;

  return (
    <AdminProtectedScreen title="Audit receipt" description="Inspect detailed evidence for an audited workflow action.">
      <AuditReceiptPage actionRequestId={actionRequestId} />
    </AdminProtectedScreen>
  );
}
