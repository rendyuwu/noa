import type { ReactNode } from "react";

import { ChatProtectedScreen } from "@/components/layout/chat-protected-screen";
import { requireServerUser } from "@/components/lib/auth/server-session";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime/runtime-provider";

export default async function AssistantLayout({ children }: { children: ReactNode }) {
  await requireServerUser("/assistant");

  return (
    <NoaAssistantRuntimeProvider>
      <ChatProtectedScreen>{children}</ChatProtectedScreen>
    </NoaAssistantRuntimeProvider>
  );
}
