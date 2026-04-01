"use client";

import { useId, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import { ChevronRightIcon } from "@radix-ui/react-icons";

import { DetailSections } from "@/components/assistant/detail-sections";
import { WorkflowReceiptSurface } from "@/components/assistant/workflow-receipt-renderer";
import {
  canWriteClipboardPng,
  captureElementToPngBlob,
  copyPngBlobToClipboard,
  downloadBlob,
} from "@/components/lib/image-export";

type ReceiptOutcome = "changed" | "partial" | "no_op" | "failed" | "denied" | "info";

type ReceiptBadge = {
  label: "SUCCESS" | "PARTIAL" | "NO-OP" | "FAILED" | "DENIED";
  className: string;
};

type ReceiptReplyTemplate = {
  title: string;
  outcome: ReceiptOutcome;
  summary: string;
  nextStep?: string | null;
};

type ReceiptEvidenceItem = {
  label: string;
  value: string;
};

type ReceiptEvidenceSection = {
  key?: string;
  title: string;
  items: ReceiptEvidenceItem[];
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

function normalizeText(value: string): string {
  return value.trim().toLowerCase();
}

function includesLoose(haystack: string, needle: string): boolean {
  return normalizeText(haystack).includes(normalizeText(needle));
}

function getBadge(outcome: ReceiptOutcome): ReceiptBadge {
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

function getPreviewSections(sections: ReceiptEvidenceSection[]) {
  const requested =
    sections.find((section) => section.key === "requested_change") ??
    sections.find((section) => includesLoose(section.title, "requested"));
  const verification =
    sections.find((section) => section.key === "verification") ??
    sections.find((section) => includesLoose(section.title, "verification"));

  const nonEmpty = sections.filter((section) => section.items.length > 0);
  const change = requested ?? nonEmpty[0];
  const verificationFallback = nonEmpty.find((section) => section !== change) ?? nonEmpty[1];
  const verify = verification ?? verificationFallback;

  return { change, verify };
}

function clampStyle(lines: number) {
  return {
    display: "-webkit-box",
    WebkitBoxOrient: "vertical" as const,
    WebkitLineClamp: lines,
    overflow: "hidden",
  };
}

async function captureStandaloneReceiptPngBlob(
  payload: Record<string, unknown>,
  {
    actionRequestId,
  }: {
    actionRequestId?: string;
  } = {},
): Promise<Blob> {
  const container = document.createElement("div");
  container.style.position = "fixed";
  container.style.top = "0";
  // Keep the capture root off-screen but not absurdly far, to avoid
  // browser/layout edge cases during HTML->image rendering.
  container.style.left = "-10000px";
  container.style.width = "900px";
  container.style.pointerEvents = "none";
  container.style.zIndex = "-1";
  document.body.appendChild(container);

  const root = createRoot(container);

  try {
    root.render(
      <div className="w-[52rem] p-6">
        <WorkflowReceiptSurface
          payload={payload}
          className="w-[52rem]"
          captureId={actionRequestId ? `thread-${actionRequestId}` : "thread"}
          openMode="export"
        />
      </div>,
    );

    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    const captureEl = container.querySelector("[data-receipt-capture]");
    if (!(captureEl instanceof HTMLElement)) {
      throw new Error("Receipt capture element unavailable");
    }
    return await captureElementToPngBlob(captureEl);
  } finally {
    try {
      root.unmount();
    } catch {}
    container.remove();
  }
}

function ReceiptCard({ payload }: { payload: Record<string, unknown> }) {
  const [copyImageState, setCopyImageState] = useState<
    "idle" | "copied" | "failed"
  >("idle");
  const [downloadState, setDownloadState] = useState<"idle" | "done" | "failed">("idle");
  const [imageBusy, setImageBusy] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const baseId = useId();
  const toggleId = `${baseId}-receipt-details-toggle`;
  const panelId = `${baseId}-receipt-details-panel`;

  const replyTemplate = coerceReplyTemplate(payload.replyTemplate);
  const evidenceSections = useMemo(
    () => coerceEvidenceSections(payload.evidenceSections),
    [payload.evidenceSections],
  );

  const actionRequestId = coerceString(payload.actionRequestId);
  const toolRunId = coerceString(payload.toolRunId);
  const toolName = coerceString(payload.toolName);

  if (!replyTemplate) {
    return (
      <div className="mt-3 rounded-xl border border-border bg-surface/70 p-3 text-sm text-muted">
        Workflow receipt unavailable.
      </div>
    );
  }

  const badge = getBadge(replyTemplate.outcome);
  const sectionsForDetail = [
    {
      title: "Overview",
      items: [
        { label: "Status", value: badge.label },
        { label: "Tool", value: toolName ?? "workflow_receipt" },
        { label: "Action ID", value: actionRequestId ?? "" },
        { label: "Tool run ID", value: toolRunId ?? "" },
      ].filter((item) => item.value.trim().length > 0),
    },
    ...evidenceSections.map((section) => ({ title: section.title, items: section.items })),
  ].filter((section) => section.items.length > 0);

  const { change, verify } = useMemo(() => getPreviewSections(evidenceSections), [evidenceSections]);
  const changeItems = change?.items.slice(0, 2) ?? [];
  const verifyItems = verify?.items.slice(0, 2) ?? [];
  const shownCount = changeItems.length + verifyItems.length;
  const totalCount = evidenceSections.reduce((sum, section) => sum + section.items.length, 0);
  const moreCount = Math.max(0, totalCount - shownCount);

  const rawJson = useMemo(() => {
    if (!detailsOpen) return null;
    try {
      return JSON.stringify(payload, null, 2);
    } catch {
      return null;
    }
  }, [detailsOpen, payload]);

  const sectionsForInline = useMemo(() => {
    if (!rawJson) return sectionsForDetail;
    return [
      ...sectionsForDetail,
      {
        title: "Raw JSON",
        items: [{ label: "Payload", value: rawJson }],
      },
    ];
  }, [rawJson, sectionsForDetail]);

  const canCopyImage = canWriteClipboardPng();

  const copyImage = async () => {
    if (typeof window === "undefined") return;

    if (!canWriteClipboardPng()) {
      setCopyImageState("failed");
      window.setTimeout(() => setCopyImageState("idle"), 1400);
      return;
    }

    setImageBusy(true);
    setCopyImageState("idle");

    try {
      const blobPromise = captureStandaloneReceiptPngBlob(payload, { actionRequestId });
      await copyPngBlobToClipboard(blobPromise);
      setCopyImageState("copied");
    } catch {
      setCopyImageState("failed");
    } finally {
      setImageBusy(false);
      window.setTimeout(() => setCopyImageState("idle"), 1400);
    }
  };

  const downloadPng = async () => {
    if (typeof window === "undefined") return;
    setImageBusy(true);
    setDownloadState("idle");
    try {
      const blob = await captureStandaloneReceiptPngBlob(payload, { actionRequestId });
      downloadBlob(blob, `receipt-${actionRequestId ?? "workflow"}.png`);
      setDownloadState("done");
    } catch {
      setDownloadState("failed");
    } finally {
      setImageBusy(false);
      window.setTimeout(() => setDownloadState("idle"), 1400);
    }
  };

  const renderPreviewBlock = (
    title: string,
    items: ReceiptEvidenceItem[],
  ) => {
    if (items.length === 0) return null;
    return (
      <div className="rounded-lg border border-border/60 bg-bg/25 px-3 py-2">
        <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
          {title}
        </div>
        <div className="mt-2 space-y-1.5">
          {items.map((item) => (
            <div key={`${title}-${item.label}-${item.value}`} className="min-w-0 text-xs text-muted">
              <span className="font-medium text-text">{item.label}</span>
              <span className="text-muted">: </span>
              <span className="break-words" style={clampStyle(1)}>
                {item.value}
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="mt-3 rounded-2xl border border-border/60 bg-bg/10 px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-text">{replyTemplate.title}</div>
        </div>
        <div
          className={[
            "inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em]",
            badge.className,
          ].join(" ")}
        >
          {badge.label}
        </div>
      </div>

      <div className="mt-1 text-xs text-muted" style={clampStyle(2)}>
        {replyTemplate.summary}
      </div>

      <div className="mt-3 grid gap-2">
        {renderPreviewBlock("Change", changeItems)}
        {renderPreviewBlock("Verification", verifyItems)}
        {moreCount > 0 ? (
          <div className="px-1 text-[11px] text-muted">+{moreCount} more</div>
        ) : null}
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          id={toggleId}
          aria-expanded={detailsOpen}
          aria-controls={panelId}
          onClick={() => setDetailsOpen((value) => !value)}
          className="inline-flex h-8 items-center justify-center gap-1 rounded-lg border border-border bg-transparent px-3 text-xs font-medium text-muted transition hover:bg-surface-2 hover:text-text"
        >
          {detailsOpen ? "Hide details" : "Details"}
          <ChevronRightIcon
            width={14}
            height={14}
            className={[
              "transition-transform duration-200 motion-reduce:transition-none",
              detailsOpen ? "rotate-90" : "rotate-0",
            ].join(" ")}
            aria-hidden="true"
          />
        </button>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={copyImage}
            disabled={imageBusy}
            title={
              canCopyImage
                ? undefined
                : "Copy image requires HTTPS (or localhost) and browser support for ClipboardItem."
            }
            className="inline-flex h-8 items-center justify-center rounded-lg border border-border bg-transparent px-3 text-xs font-medium text-muted transition hover:bg-surface-2 hover:text-text active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {copyImageState === "copied"
              ? "Copied"
              : copyImageState === "failed"
                ? "Failed"
                : "Copy image"}
          </button>
          <button
            type="button"
            onClick={downloadPng}
            disabled={imageBusy}
            className="inline-flex h-8 items-center justify-center rounded-lg border border-border bg-transparent px-3 text-xs font-medium text-muted transition hover:bg-surface-2 hover:text-text active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {downloadState === "done"
              ? "Downloaded"
              : downloadState === "failed"
                ? "Failed"
                : "Download PNG"}
          </button>
        </div>
      </div>

      <div
        id={panelId}
        role="region"
        aria-labelledby={toggleId}
        hidden={!detailsOpen}
        className="mt-3"
      >
        {detailsOpen ? (
          <div className="rounded-xl border border-border bg-bg/15 px-3 py-3">
            <DetailSections sections={sectionsForInline} variant="inline" showEmptyState />
          </div>
        ) : null}
      </div>
    </div>
  );
}

export const WorkflowReceiptToolUI = makeAssistantToolUI({
  toolName: "workflow_receipt",
  render: ({
    args,
    result,
  }: {
    args: Record<string, unknown>;
    result?: unknown;
    status?: unknown;
  }) => {
    const payload = coerceRecord(result) ?? args;
    return <ReceiptCard payload={payload} />;
  },
});
