"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { WorkflowReceiptSurface } from "@/components/assistant/workflow-receipt-renderer";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";
import { Skeleton } from "@/components/ui/skeleton";

export function AuditReceiptPage({ actionRequestId }: { actionRequestId: string }) {
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSeqRef = useRef(0);

  useEffect(() => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setLoadError(null);
    setPayload(null);

    void (async () => {
      try {
        const response = await fetchWithAuth(`/admin/audit/action-requests/${actionRequestId}/receipt`);
        const receipt = await jsonOrThrow<Record<string, unknown>>(response);

        if (seq !== loadSeqRef.current) {
          return;
        }

        setPayload(receipt);
      } catch (error) {
        if (seq !== loadSeqRef.current) {
          return;
        }

        setLoadError(toErrorMessage(error, "Unable to load receipt"));
      } finally {
        if (seq === loadSeqRef.current) {
          setLoading(false);
        }
      }
    })();

    return () => {
      loadSeqRef.current += 1;
    };
  }, [actionRequestId]);

  return (
    <section className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold text-text">Receipt {actionRequestId}</h2>
          <p className="mt-1 text-sm text-muted">Standalone action receipt details.</p>
        </div>
        <Link href="/admin/audit" className="text-sm text-accent underline underline-offset-2">
          Back to audit
        </Link>
      </div>

      {loadError ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900" role="alert">
          {loadError}
        </div>
      ) : null}

      {payload ? (
        <WorkflowReceiptSurface payload={payload} captureId={`audit-${actionRequestId}`} className="max-w-4xl" />
      ) : (
        loading ? (
          <div className="space-y-3">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-32 w-full rounded-xl" />
            <Skeleton className="h-20 w-full rounded-xl" />
          </div>
        ) : (
          <div className="rounded-xl border border-border bg-surface/70 px-4 py-4 font-ui text-sm text-muted">
            Receipt unavailable.
          </div>
        )
      )}
    </section>
  );
}
