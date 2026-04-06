"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { useAssistantApi, useAssistantState } from "@assistant-ui/react";

import { reportClientError } from "@/components/lib/observability/error-reporting";
import { getActiveThreadListItem } from "@/components/lib/runtime/assistant-thread-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export function RouteThreadSync({ routeThreadId }: { routeThreadId?: string | null }) {
  const api = useAssistantApi();
  const router = useRouter();
  const activeRemoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const messageCount = useAssistantState(({ thread }) => thread.messages.length);
  const [routeError, setRouteError] = useState<string | null>(null);
  const lastRouteKey = useRef<string | null>(null);

  useEffect(() => {
    const normalizedRouteThreadId = typeof routeThreadId === "string" && routeThreadId.trim() ? routeThreadId : null;
    const routeKey = normalizedRouteThreadId ? `thread:${normalizedRouteThreadId}` : "new";

    if (lastRouteKey.current === routeKey) {
      return;
    }

    lastRouteKey.current = routeKey;
    let cancelled = false;

    void (async () => {
      try {
        if (!normalizedRouteThreadId) {
          setRouteError(null);
          return;
        }

        if (activeRemoteId === normalizedRouteThreadId) {
          setRouteError(null);
          return;
        }

        if (!activeRemoteId && messageCount > 0) {
          setRouteError(null);
          return;
        }

        await api.threads().switchToThread(normalizedRouteThreadId);
        if (!cancelled) {
          setRouteError(null);
        }
      } catch (error) {
        reportClientError(error, {
          routeThreadId: normalizedRouteThreadId,
          source: "assistant.route-thread-sync",
        });
        if (!cancelled) {
          setRouteError("This chat link is invalid or no longer available.");
        }
        router.replace("/assistant", { scroll: false });
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
  }, [activeRemoteId, api, messageCount, routeThreadId, router]);

  if (!routeError) {
    return null;
  }

  return (
    <Alert tone="warning" className="mb-4">
      <ShieldAlert />
      <div>
        <AlertTitle>Chat link unavailable</AlertTitle>
        <AlertDescription>{routeError}</AlertDescription>
      </div>
    </Alert>
  );
}
