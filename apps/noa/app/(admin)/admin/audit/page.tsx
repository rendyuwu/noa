import { PlaceholderAdminRoute } from "@/components/admin/placeholder-admin-route";

export default function AdminAuditPage() {
  return (
    <PlaceholderAdminRoute
      eyebrow="Admin"
      title="Audit"
      description="Shared shell route for audit list, filters, and receipt navigation."
      placeholderDescription="The receipt route and list page now have dedicated destinations inside the shared admin shell, ready for cursor/filter parity work."
    />
  );
}
