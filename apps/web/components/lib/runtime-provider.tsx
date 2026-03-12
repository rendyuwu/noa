"use client";

import type { PropsWithChildren } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  unstable_useRemoteThreadListRuntime as useRemoteThreadListRuntime,
  useAssistantApi,
  useAssistantRuntime,
  useAssistantState,
  useAssistantTransportRuntime,
} from "@assistant-ui/react";

import {
  type AssistantState,
  convertAssistantState,
} from "@/components/lib/assistant-transport-converter";
import { getAuthToken } from "@/components/lib/auth-store";
import { fetchWithAuth, getApiUrl, jsonOrThrow } from "@/components/lib/fetch-helper";
import { ThreadHydrationProvider } from "@/components/lib/thread-hydration";
import { threadListAdapter } from "@/components/lib/thread-list-adapter";

function ThreadMaintenanceProvider({ children }: PropsWithChildren) {
  const api = useAssistantApi();
  const runtime = useAssistantRuntime();
  const remoteId = useAssistantState(({ threadListItem }) => threadListItem.remoteId);
  const messageCount = useAssistantState(({ thread }) => thread.messages.length);
  const threadItems = useAssistantState(({ threads }) => threads.threadItems);

  const [hydratedRemoteId, setHydratedRemoteId] = useState<string | null>(null);
  const generatedTitles = useRef<Set<string>>(new Set());

  const shouldHydrate = Boolean(remoteId) && messageCount === 0 && hydratedRemoteId !== remoteId;

  useEffect(() => {
    if (!remoteId) return;
    if (!shouldHydrate) return;

    let cancelled = false;

    void (async () => {
      try {
        const response = await fetchWithAuth(`/assistant/threads/${remoteId}/state`);
        const persistedState = await jsonOrThrow<AssistantState>(response);
        if (cancelled) return;

        runtime.thread.unstable_loadExternalState(persistedState);
      } catch (error) {
        console.error("Failed to hydrate persisted thread state", error);
      } finally {
        if (!cancelled) setHydratedRemoteId(remoteId);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [remoteId, runtime, shouldHydrate]);

  useEffect(() => {
    let remaining = 3;

    for (const item of threadItems) {
      if (remaining <= 0) break;
      if (!item.remoteId) continue;
      if (item.status !== "regular") continue;
      if (item.title && item.title.trim()) continue;
      if (generatedTitles.current.has(item.id)) continue;

      generatedTitles.current.add(item.id);
      api.threads().item({ id: item.id }).generateTitle();
      remaining -= 1;
    }
  }, [api, threadItems]);

  return <ThreadHydrationProvider isHydrating={shouldHydrate}>{children}</ThreadHydrationProvider>;
}

function useThreadAwareAssistantTransportRuntime() {
  const api = useAssistantApi();
  const remoteId = useAssistantState(({ threadListItem }) => threadListItem.remoteId);

  const ensureThreadId = useCallback(async () => {
    if (remoteId) {
      return remoteId;
    }

    const threadListItem = api.threadListItem();
    const current = threadListItem.getState();
    if (current.remoteId) {
      return current.remoteId;
    }

    const initialized = await threadListItem.initialize();
    if (initialized.remoteId) {
      return initialized.remoteId;
    }

    throw new Error("Unable to resolve thread id before sending assistant commands");
  }, [api, remoteId]);

  return useAssistantTransportRuntime({
    api: `${getApiUrl()}/assistant`,
    protocol: "assistant-transport",
    initialState: {
      messages: [],
      isRunning: false,
    },
    converter: convertAssistantState,
    body: async () => ({
      threadId: await ensureThreadId(),
    }),
    headers: async () => {
      const token = getAuthToken();
      return token ? { authorization: `Bearer ${token}` } : {};
    },
    onError: (error, { commands }) => {
      console.error("Assistant transport error", error, { commands: commands.length });
    },
  });
}

export function NoaAssistantRuntimeProvider({ children }: PropsWithChildren) {
  const runtime = useRemoteThreadListRuntime({
    adapter: threadListAdapter,
    runtimeHook: () => useThreadAwareAssistantTransportRuntime(),
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadMaintenanceProvider>{children}</ThreadMaintenanceProvider>
    </AssistantRuntimeProvider>
  );
}
