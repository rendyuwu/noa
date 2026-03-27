"use client";

import { useRequireAuth } from "@/components/lib/auth-store";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime-provider";
import { ClaudeWorkspace } from "@/components/assistant/claude-workspace";

export function AssistantApp() {
  const ready = useRequireAuth();

  if (!ready) {
    return null;
  }

  return (
    <main className="min-h-dvh bg-bg p-0">
      <NoaAssistantRuntimeProvider>
        <ClaudeWorkspace />
      </NoaAssistantRuntimeProvider>
    </main>
  );
}
