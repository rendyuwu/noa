"use client";

import type { PropsWithChildren } from "react";

import { useRequireAuth } from "@/components/lib/auth-store";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime-provider";

export function AssistantApp({ children }: PropsWithChildren) {
  const ready = useRequireAuth();

  if (!ready) {
    return null;
  }

  return (
    <main className="min-h-dvh bg-background p-0">
      <NoaAssistantRuntimeProvider>
        {children}
      </NoaAssistantRuntimeProvider>
    </main>
  );
}
