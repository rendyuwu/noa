import type { ReactNode } from "react";

import { ProtectedScreen } from "@/components/layout/protected-screen";

export default function AssistantLayout({ children }: { children: ReactNode }) {
  return (
    <ProtectedScreen
      title="Assistant"
      description="Shared browser shell, auth contract, and same-origin transport scaffold for the NOA rewrite."
    >
      {children}
    </ProtectedScreen>
  );
}
