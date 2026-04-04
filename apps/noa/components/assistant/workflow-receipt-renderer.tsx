"use client";

import { useMemo } from "react";

type ReceiptOutcome = "changed" | "partial" | "no_op" | "failed" | "denied" | "info";

export type ReceiptBadge = {
  label: "SUCCESS" | "PARTIAL" | "NO-OP" | "FAILED" | "DENIED";
  className: string;
};

type ReceiptReplyTemplate = {
  title: string;
  outcome: ReceiptOutcome;
  summary: string;
  nextStep?: string | null;
};

export type ReceiptEvidenceItem = {
  label: string;
  value: string;
};

export type ReceiptEvidenceSection = {
  key?: string;
  title: string;
  items: ReceiptEvidenceItem[];
};

export type WorkflowReceiptParsed = {
  replyTemplate: ReceiptReplyTemplate;
  badge: ReceiptBadge;
  evidenceSections: ReceiptEvidenceSection[];
  actionRequestId?: string;
  threadId?: string;
  toolRunId?: string;
  receiptId?: string;
  toolName?: string;
  workflowFamily?: string;
  terminalPhase?: string;
  generatedAt?: string;
  errorCode?: string;
};

function coerceString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function coerceRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function coerceReplyTemplate(value: unknown): ReceiptReplyTemplate | undefined {
  const record = coerceRecord(value);
  const title = coerceString(record?.title);
  const summary = coerceString(record?.summary);
  const outcome = coerceString(record?.outcome);
  const nextStep = coerceString(record?.nextStep);

  const isOutcome =
    outcome === "changed" ||
    outcome === "partial" ||
    outcome === "no_op" ||
    outcome === "failed" ||
    outcome === "denied" ||
    outcome === "info";

  if (!title || !summary || !isOutcome) {
    return undefined;
  }

  return {
    title,
    summary,
    outcome,
    nextStep: nextStep ?? null,
  };
}

function coerceEvidenceSections(value: unknown): ReceiptEvidenceSection[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.flatMap((entry) => {
    const record = coerceRecord(entry);
    const title = coerceString(record?.title);
    const key = coerceString(record?.key);
    const items = Array.isArray(record?.items)
      ? record.items
          .map((item) => {
            const itemRecord = coerceRecord(item);
            const label = coerceString(itemRecord?.label);
            const rawValue = itemRecord?.value;

            if (!label) {
              return undefined;
            }

            if (typeof rawValue === "string") {
              return { label, value: rawValue };
            }

            if (typeof rawValue === "number" || typeof rawValue === "boolean") {
              return { label, value: String(rawValue) };
            }

            return undefined;
          })
          .filter((item): item is ReceiptEvidenceItem => Boolean(item))
      : [];

    if (!title || items.length === 0) {
      return [];
    }

    return [{ key, title, items }];
  });
}

export function getReceiptBadge(outcome: ReceiptOutcome): ReceiptBadge {
  switch (outcome) {
    case "changed":
      return {
        label: "SUCCESS",
        className: "bg-emerald-100 text-emerald-900",
      };
    case "partial":
      return {
        label: "PARTIAL",
        className: "bg-amber-100 text-amber-900",
      };
    case "no_op":
    case "info":
      return {
        label: "NO-OP",
        className: "bg-surface-2 text-muted",
      };
    case "failed":
      return {
        label: "FAILED",
        className: "bg-red-100 text-red-900",
      };
    case "denied":
      return {
        label: "DENIED",
        className: "bg-surface-2 text-muted",
      };
  }
}

export function parseWorkflowReceiptPayload(payload: Record<string, unknown>): WorkflowReceiptParsed | null {
  const replyTemplate = coerceReplyTemplate(payload.replyTemplate);

  if (!replyTemplate) {
    return null;
  }

  return {
    replyTemplate,
    badge: getReceiptBadge(replyTemplate.outcome),
    evidenceSections: coerceEvidenceSections(payload.evidenceSections),
    actionRequestId: coerceString(payload.actionRequestId),
    threadId: coerceString(payload.threadId),
    toolRunId: coerceString(payload.toolRunId),
    receiptId: coerceString(payload.receiptId),
    toolName: coerceString(payload.toolName),
    workflowFamily: coerceString(payload.workflowFamily),
    terminalPhase: coerceString(payload.terminalPhase),
    generatedAt: coerceString(payload.generatedAt),
    errorCode: coerceString(payload.errorCode),
  };
}

export function WorkflowReceiptContent({ payload }: { payload: Record<string, unknown> }) {
  const parsed = useMemo(() => parseWorkflowReceiptPayload(payload), [payload]);

  if (!parsed) {
    return (
      <div className="rounded-xl border border-border bg-surface/60 px-4 py-3 font-ui text-sm text-muted">
        Workflow receipt unavailable.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-base font-semibold text-text">{parsed.replyTemplate.title}</div>
          <div className="mt-1 text-sm text-muted">{parsed.replyTemplate.summary}</div>
        </div>
        <div
          className={[
            "inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em]",
            parsed.badge.className,
          ].join(" ")}
        >
          {parsed.badge.label}
        </div>
      </div>

      {parsed.evidenceSections.map((section) => (
        <section key={section.title} className="rounded-xl border border-border bg-bg/20 px-3 py-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">{section.title}</h3>
          <dl className="mt-2 space-y-2 text-sm">
            {section.items.map((item, index) => (
              <div key={`${section.title}-${item.label}-${index}`} className="grid grid-cols-[8rem_minmax(0,1fr)] gap-2">
                <dt className="text-muted">{item.label}</dt>
                <dd className="min-w-0 break-words text-text">{item.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}

      {parsed.replyTemplate.nextStep ? (
        <div className="rounded-xl border border-border bg-bg/20 px-3 py-3 text-sm text-muted">
          <strong className="text-text">Next step:</strong> {parsed.replyTemplate.nextStep}
        </div>
      ) : null}
    </div>
  );
}

export function WorkflowReceiptSurface({
  payload,
  className,
  captureId,
}: {
  payload: Record<string, unknown>;
  className?: string;
  captureId?: string;
}) {
  return (
    <div
      data-receipt-capture={captureId ?? "workflow"}
      className={["rounded-2xl border border-border bg-surface p-4", className].filter(Boolean).join(" ")}
    >
      <WorkflowReceiptContent payload={payload} />
    </div>
  );
}
