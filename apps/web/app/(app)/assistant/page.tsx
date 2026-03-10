"use client";

import { useRequireAuth } from "@/components/lib/auth-store";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime-provider";
import { ClaudeWorkspace } from "@/components/claude/claude-workspace";

export default function AssistantPage() {
  const ready = useRequireAuth();

  if (!ready) {
    return null;
  }

  return (
    <main className="min-h-dvh bg-[#F5F5F0] p-0 dark:bg-[#2b2a27]">
      <NoaAssistantRuntimeProvider>
        <ClaudeWorkspace />
      </NoaAssistantRuntimeProvider>
    </main>
  );
}
