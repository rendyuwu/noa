import { AuditReceiptPage } from "@/components/admin/audit-receipt-page";
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
      description="Inspect detailed evidence for an audited workflow action."
    >
      <AuditReceiptPage actionRequestId={actionRequestId} />
    </ProtectedScreen>
  );
}
