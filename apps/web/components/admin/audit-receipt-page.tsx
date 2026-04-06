"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { WorkflowReceiptSurface } from "@/components/assistant/workflow-receipt-renderer";
import { Button } from "@/components/lib/button";
import { toUserMessage } from "@/components/lib/error-message";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";
import {
  canWriteClipboardPng,
  captureElementToPngBlob,
  copyPngBlobToClipboard,
  downloadBlob,
} from "@/components/lib/image-export";

export function AuditReceiptPage({ actionRequestId }: { actionRequestId: string }) {
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [copyImageState, setCopyImageState] = useState<
    "idle" | "copied" | "failed"
  >("idle");
  const [downloadState, setDownloadState] = useState<"idle" | "done" | "failed">("idle");

  const loadSeqRef = useRef(0);
  const captureWrapperRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setLoadError(null);
    setPayload(null);

    void (async () => {
      try {
        const response = await fetchWithAuth(`/admin/audit/action-requests/${actionRequestId}/receipt`);
        const receipt = await jsonOrThrow<Record<string, unknown>>(response);
        if (seq !== loadSeqRef.current) return;
        setPayload(receipt);
      } catch (error) {
        if (seq !== loadSeqRef.current) return;
        setLoadError(toUserMessage(error, "Unable to load receipt"));
      } finally {
        if (seq !== loadSeqRef.current) return;
        setLoading(false);
      }
    })();

    return () => {
      loadSeqRef.current += 1;
    };
  }, [actionRequestId]);

  const canCopyImage = canWriteClipboardPng();

  const resolveCaptureElement = useCallback((): HTMLElement | null => {
    const root = captureWrapperRef.current;
    if (!root) return null;
    const el = root.querySelector("[data-receipt-capture]");
    return el instanceof HTMLElement ? el : null;
  }, []);

  const capturePngBlob = useCallback(async (): Promise<Blob> => {
    const el = resolveCaptureElement();
    if (!el) throw new Error("Receipt capture element unavailable");
    return await captureElementToPngBlob(el);
  }, [resolveCaptureElement]);

  const copyImage = useCallback(async () => {
    if (!payload) return;
    setCopyImageState("idle");
    try {
      if (!canWriteClipboardPng()) {
        throw new Error("Clipboard image write unsupported");
      }
      const blobPromise = capturePngBlob();
      await copyPngBlobToClipboard(blobPromise);
      setCopyImageState("copied");
    } catch {
      setCopyImageState("failed");
    }
    window.setTimeout(() => setCopyImageState("idle"), 1400);
  }, [capturePngBlob, payload]);

  const downloadPng = useCallback(async () => {
    if (!payload) return;
    setDownloadState("idle");
    try {
      const blob = await capturePngBlob();
      downloadBlob(blob, `receipt-${actionRequestId}.png`);
      setDownloadState("done");
      window.setTimeout(() => setDownloadState("idle"), 1400);
    } catch {
      setDownloadState("failed");
      window.setTimeout(() => setDownloadState("idle"), 1400);
    }
  }, [actionRequestId, capturePngBlob, payload]);

  return (
    <main className="min-h-dvh bg-bg p-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold">Receipt</h1>
          <p className="mt-1 font-ui text-sm text-muted">Standalone, export-friendly receipt view.</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/admin/audit"
            className="font-ui text-sm text-muted underline decoration-border/60 underline-offset-4 hover:text-text hover:decoration-border"
          >
            Back to Audit
          </Link>
        </div>
      </div>

      {loadError ? (
        <div
          role="alert"
          aria-live="assertive"
          className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 font-ui text-sm text-red-800"
        >
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">{loadError}</div>
            <Button size="sm" onClick={() => window.location.reload()}>
              Reload
            </Button>
          </div>
        </div>
      ) : null}

      <div className="mt-6 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={copyImage}
            disabled={!payload || loading}
            title={
              canCopyImage
                ? undefined
                : "Copy image requires HTTPS (or localhost) and browser support for ClipboardItem."
            }
          >
            {copyImageState === "copied"
              ? "Copied"
              : copyImageState === "failed"
                ? "Failed"
                : "Copy image"}
          </Button>
          <Button size="sm" variant="secondary" onClick={downloadPng} disabled={!payload || loading}>
            {downloadState === "done" ? "Downloaded" : downloadState === "failed" ? "Download failed" : "Download PNG"}
          </Button>
        </div>
      </div>

      <div className="mt-4" ref={captureWrapperRef}>
        {payload ? (
          <div className="mx-auto w-full max-w-[52rem]">
            <WorkflowReceiptSurface
              payload={payload}
              className="w-full"
              captureId={`audit-${actionRequestId}`}
              openMode="export"
            />
          </div>
        ) : (
          <div className="panel mx-auto w-full max-w-[52rem] p-6 font-ui text-sm text-muted">
            {loading ? "Loading receipt..." : "Receipt unavailable."}
          </div>
        )}
      </div>
    </main>
  );
}
