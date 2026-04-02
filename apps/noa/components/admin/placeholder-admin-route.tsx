import { notFound } from "next/navigation";

import { ProtectedScreen } from "@/components/layout/protected-screen";

import { AdminPlaceholder } from "./admin-placeholder";
import { isPlaceholderAdminRouteEnabled } from "./lib/placeholder-route-access";

type PlaceholderAdminRouteProps = {
  description: string;
  eyebrow: string;
  placeholderDescription: string;
  title: string;
};

export function PlaceholderAdminRoute({
  description,
  eyebrow,
  placeholderDescription,
  title,
}: PlaceholderAdminRouteProps) {
  if (!isPlaceholderAdminRouteEnabled()) {
    notFound();
  }

  return (
    <ProtectedScreen requireAdmin title={title} description={description}>
      <AdminPlaceholder
        eyebrow={`${eyebrow} / Preview`}
        title={title}
        description={`${placeholderDescription} This route is intentionally hidden from the default production surface until the underlying workflow reaches feature parity.`}
      />
    </ProtectedScreen>
  );
}
