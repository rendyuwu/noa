"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { useAssistantApi, useAssistantState } from "@assistant-ui/react";

import { reportClientError } from "@/components/lib/observability/error-reporting";
import { getActiveThreadListItem } from "@/components/lib/runtime/assistant-thread-state";

export function RouteThreadSync({ routeThreadId }: { routeThreadId?: string | null }) {
  const api = useAssistantApi();
  const activeRemoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const [routeError, setRouteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        if (!routeThreadId) {
          setRouteError(null);
          return;
        }

        if (activeRemoteId === routeThreadId) {
          setRouteError(null);
          return;
        }

        await api.threads().switchToThread(routeThreadId);
        if (!cancelled) {
          setRouteError(null);
        }
      } catch (error) {
        reportClientError(error, {
          routeThreadId,
          source: "assistant.route-thread-sync",
        });
        if (!cancelled) {
          setRouteError("This chat link is invalid or no longer available.");
        }
        try {
          await api.threads().switchToNewThread();
        } catch {
          // Keep the original route sync failure surfaced to the user.
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeRemoteId, api, routeThreadId]);

  if (!routeError) {
    return null;
  }

  return (
    <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 font-ui text-sm text-amber-800">
      <div className="flex items-center gap-2">
        <ShieldAlert className="size-4" />
        {routeError}
      </div>
    </div>
  );
}
