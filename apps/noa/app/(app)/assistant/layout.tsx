import type { ReactNode } from "react";

import { ProtectedScreen } from "@/components/layout/protected-screen";
import { requireServerUser } from "@/components/lib/auth/server-session";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime/runtime-provider";

export default async function AssistantLayout({ children }: { children: ReactNode }) {
  await requireServerUser("/assistant");

  return (
    <ProtectedScreen
      title="Assistant"
      description="NOA Assistant — AI-powered workspace with persisted conversations"
    >
      <NoaAssistantRuntimeProvider>{children}</NoaAssistantRuntimeProvider>
    </ProtectedScreen>
  );
}
