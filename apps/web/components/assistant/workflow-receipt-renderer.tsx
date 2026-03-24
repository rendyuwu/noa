"use client";

import { useMemo } from "react";

import { DetailSections } from "@/components/assistant/detail-sections";
import type {
  AssistantDetailEvidenceItem,
  AssistantDetailEvidenceSection,
} from "@/components/assistant/approval-state";

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

  if (!title || !summary || !isOutcome) return undefined;

  return {
    title,
    summary,
    outcome,
    nextStep: nextStep ?? null,
  };
}

function coerceEvidenceSections(value: unknown): ReceiptEvidenceSection[] {
  if (!Array.isArray(value)) return [];
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
            if (!label) return undefined;
            if (typeof rawValue === "string") return { label, value: rawValue };
            if (typeof rawValue === "number" || typeof rawValue === "boolean") {
              return { label, value: String(rawValue) };
            }
            return undefined;
          })
          .filter((item): item is ReceiptEvidenceItem => Boolean(item))
      : [];
    if (!title || items.length === 0) return [];
    return [{ key, title, items }];
  });
}

export function getReceiptBadge(outcome: ReceiptOutcome): ReceiptBadge {
  switch (outcome) {
    case "changed":
      return {
        label: "SUCCESS",
        className: "bg-emerald-500/10 text-emerald-200 ring-1 ring-emerald-500/25",
      };
    case "partial":
      return {
        label: "PARTIAL",
        className: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/25",
      };
    case "no_op":
    case "info":
      return {
        label: "NO-OP",
        className: "bg-surface-2 text-muted ring-1 ring-border/40",
      };
    case "failed":
      return {
        label: "FAILED",
        className: "bg-rose-500/10 text-rose-200 ring-1 ring-rose-500/25",
      };
    case "denied":
      return {
        label: "DENIED",
        className: "bg-slate-500/10 text-slate-200 ring-1 ring-slate-500/25",
      };
  }
}

export function parseWorkflowReceiptPayload(payload: Record<string, unknown>): WorkflowReceiptParsed | null {
  const replyTemplate = coerceReplyTemplate(payload.replyTemplate);
  if (!replyTemplate) return null;

  const evidenceSections = coerceEvidenceSections(payload.evidenceSections);
  const badge = getReceiptBadge(replyTemplate.outcome);

  return {
    replyTemplate,
    badge,
    evidenceSections,
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

function buildOverviewItems(parsed: WorkflowReceiptParsed): AssistantDetailEvidenceItem[] {
  const items: AssistantDetailEvidenceItem[] = [];
  items.push({ label: "Status", value: parsed.badge.label });
  if (parsed.terminalPhase) items.push({ label: "Terminal phase", value: parsed.terminalPhase });
  if (parsed.toolName) items.push({ label: "Tool", value: parsed.toolName });
  if (parsed.workflowFamily) items.push({ label: "Workflow family", value: parsed.workflowFamily });
  if (parsed.generatedAt) items.push({ label: "Generated at", value: parsed.generatedAt });
  return items;
}

export function buildWorkflowReceiptSections(parsed: WorkflowReceiptParsed): AssistantDetailEvidenceSection[] {
  const overviewItems = buildOverviewItems(parsed);
  const sections: AssistantDetailEvidenceSection[] = [];

  if (overviewItems.length) {
    sections.push({ title: "Overview", items: overviewItems });
  }

  for (const section of parsed.evidenceSections) {
    sections.push({
      title: section.title,
      items: section.items,
    });
  }

  if (parsed.replyTemplate.nextStep && parsed.replyTemplate.nextStep.trim()) {
    sections.push({
      title: "Next Step",
      items: [{ label: "Next", value: parsed.replyTemplate.nextStep.trim() }],
    });
  }

  return sections;
}

export function buildWorkflowReceiptPlaintext(parsed: WorkflowReceiptParsed): string {
  const lines: string[] = [];
  lines.push(parsed.replyTemplate.title);
  lines.push(`Status: ${parsed.badge.label}`);
  if (parsed.terminalPhase) lines.push(`Terminal phase: ${parsed.terminalPhase}`);
  lines.push("");
  lines.push(parsed.replyTemplate.summary);
  lines.push("");

  for (const section of parsed.evidenceSections) {
    lines.push(`${section.title}:`);
    for (const item of section.items) {
      lines.push(`- ${item.label}: ${item.value}`);
    }
    lines.push("");
  }

  if (parsed.replyTemplate.nextStep && parsed.replyTemplate.nextStep.trim()) {
    lines.push(`Next step: ${parsed.replyTemplate.nextStep.trim()}`);
    lines.push("");
  }

  if (parsed.threadId) lines.push(`Thread ID: ${parsed.threadId}`);
  if (parsed.actionRequestId) lines.push(`Action ID: ${parsed.actionRequestId}`);
  if (parsed.toolRunId) lines.push(`Tool run ID: ${parsed.toolRunId}`);
  if (parsed.receiptId) lines.push(`Receipt ID: ${parsed.receiptId}`);
  if (parsed.errorCode) lines.push(`Error code: ${parsed.errorCode}`);

  return lines.join("\n").trim();
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

  const sections = buildWorkflowReceiptSections(parsed);

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-base font-semibold text-text">{parsed.replyTemplate.title}</div>
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
      <div className="text-sm text-muted">{parsed.replyTemplate.summary}</div>

      <DetailSections sections={sections} variant="inline" showEmptyState />
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
      className={[
        "panel",
        "bg-surface",
        "p-6",
        "[contain:layout_paint_style]",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <WorkflowReceiptContent payload={payload} />
    </div>
  );
}
