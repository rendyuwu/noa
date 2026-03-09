"use client";

import Link from "next/link";

import { clearAuth, useRequireAuth } from "@/components/lib/auth-store";
import { NoaAssistantRuntimeProvider } from "@/components/lib/runtime-provider";
import { AssistantWorkspace } from "@/components/lib/thread-shell";

export default function AssistantPage() {
  const ready = useRequireAuth();

  if (!ready) {
    return null;
  }

  return (
    <main className="page-shell">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h1 style={{ margin: 0 }}>Assistant</h1>
        <div className="row">
          <Link className="button" href="/admin">
            Admin
          </Link>
          <button className="button" onClick={clearAuth} type="button">
            Logout
          </button>
        </div>
      </div>

      <NoaAssistantRuntimeProvider>
        <AssistantWorkspace />
      </NoaAssistantRuntimeProvider>
    </main>
  );
}
