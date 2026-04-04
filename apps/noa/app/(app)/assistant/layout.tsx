import type { ReactNode } from "react";

import { ProtectedScreen } from "@/components/layout/protected-screen";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime/runtime-provider";

export default function AssistantLayout({ children }: { children: ReactNode }) {
  return (
    <ProtectedScreen
      title="Assistant"
      description="NOA Assistant — AI-powered workspace with persisted conversations"
    >
      <NoaAssistantRuntimeProvider>{children}</NoaAssistantRuntimeProvider>
    </ProtectedScreen>
  );
}
