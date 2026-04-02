"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";

import { WorkflowReceiptSurface } from "./workflow-receipt-renderer";

function coerceRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

export function WorkflowReceiptCard({ payload }: { payload: Record<string, unknown> }) {
  return (
    <div className="mt-3" data-testid="workflow-receipt-tool-ui">
      <WorkflowReceiptSurface payload={payload} />
    </div>
  );
}

export const WorkflowReceiptToolUI = makeAssistantToolUI({
  toolName: "workflow_receipt",
  render: ({ args, result }: { args: Record<string, unknown>; result?: unknown }) => {
    const payload = coerceRecord(result) ?? coerceRecord(args);

    if (!payload) {
      return (
        <div className="mt-3 rounded-xl border border-border bg-surface/70 p-3 text-sm text-muted">
          Workflow receipt unavailable.
        </div>
      );
    }

    return <WorkflowReceiptCard payload={payload} />;
  },
});
