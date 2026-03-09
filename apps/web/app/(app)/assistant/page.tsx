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
    <main className="page-shell">
      <NoaAssistantRuntimeProvider>
        <ClaudeWorkspace />
      </NoaAssistantRuntimeProvider>
    </main>
  );
}
