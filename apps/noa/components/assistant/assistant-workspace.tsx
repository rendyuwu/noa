"use client";

import { RequestApprovalToolUI } from "./assistant-tool-ui";
import { RouteThreadSync } from "./assistant-route-thread-sync";
import { ThreadPanel } from "./assistant-thread-panel";
import { ThreadSidebar } from "./assistant-thread-sidebar";

export function AssistantWorkspace({ threadId }: { threadId?: string | null }) {
  return (
    <section className="space-y-4">
      <RequestApprovalToolUI />
      <RouteThreadSync routeThreadId={threadId} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
        <ThreadSidebar />
        <ThreadPanel />
      </div>
    </section>
  );
}
