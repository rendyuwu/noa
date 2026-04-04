import type { ReactNode } from "react";

import { requireServerAdmin } from "@/components/lib/auth/server-session";

export default async function AdminLayout({ children }: { children: ReactNode }) {
  await requireServerAdmin("/admin");
  return <>{children}</>;
}
