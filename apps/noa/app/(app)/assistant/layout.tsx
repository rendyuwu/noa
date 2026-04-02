import type { ReactNode } from "react";

import { ProtectedScreen } from "@/components/layout/protected-screen";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime/runtime-provider";

export default function AssistantLayout({ children }: { children: ReactNode }) {
  return (
    <ProtectedScreen
      title="Assistant"
      description="Shared browser shell, auth contract, and same-origin transport scaffold for the NOA rewrite."
    >
      <NoaAssistantRuntimeProvider>{children}</NoaAssistantRuntimeProvider>
    </ProtectedScreen>
  );
}
