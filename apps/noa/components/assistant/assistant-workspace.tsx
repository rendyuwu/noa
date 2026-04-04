"use client";

import { useMemo } from "react";
import { useAssistantState } from "@assistant-ui/react";

import { extractLatestCanonicalActionRequests } from "./approval-state";
import { ApprovalDock } from "./approval-dock";
import { RequestApprovalToolUI } from "./assistant-tool-ui";
import { RouteThreadSync } from "./assistant-route-thread-sync";
import { ThreadHeader } from "./thread-header";
import { ThreadPanel } from "./assistant-thread-panel";
import { WorkflowDock } from "./workflow-dock";
import {
  extractLatestCanonicalWorkflowTodos,
  extractLatestWorkflowTodos,
  WorkflowTodoToolUI,
} from "./workflow-todo-tool-ui";
import { WorkflowReceiptToolUI } from "./workflow-receipt-tool-ui";

function AssistantLiveDocks() {
  const threadMessages = useAssistantState((state) => state.thread?.messages);
  const isRunning = useAssistantState((state) => Boolean(state.thread?.isRunning));

  const actionRequests = useMemo(
    () => extractLatestCanonicalActionRequests(threadMessages) ?? [],
    [threadMessages],
  );
  const workflowTodos = useMemo(() => {
    const canonical = extractLatestCanonicalWorkflowTodos(threadMessages);
    if (canonical) {
      return canonical;
    }

    return extractLatestWorkflowTodos(threadMessages);
  }, [threadMessages]);

  if (actionRequests.length === 0 && workflowTodos.length === 0) {
    return null;
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-3 px-4">
      <ApprovalDock requests={actionRequests} />
      <WorkflowDock todos={workflowTodos} isRunning={isRunning} />
    </div>
  );
}

export function AssistantWorkspace({ threadId }: { threadId?: string | null }) {
  return (
    <div className="flex flex-1 flex-col">
      <RequestApprovalToolUI />
      <WorkflowTodoToolUI />
      <WorkflowReceiptToolUI />
      <RouteThreadSync routeThreadId={threadId} />
      <ThreadHeader />
      <AssistantLiveDocks />
      <ThreadPanel />
    </div>
  );
}
