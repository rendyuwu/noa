"use client";

import type { PropsWithChildren } from "react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  AssistantRuntimeProvider,
  unstable_useRemoteThreadListRuntime as useRemoteThreadListRuntime,
  useAssistantRuntime,
  useAssistantState,
  useAssistantTransportRuntime,
} from "@assistant-ui/react";

import { getAuthToken } from "@/components/lib/auth/auth-storage";
import { getApiUrl, jsonOrThrow, fetchWithAuth } from "@/components/lib/http/fetch-client";

import { convertAssistantState, type AssistantState } from "./assistant-transport-converter";
import { getActiveThreadListItem } from "./assistant-thread-state";
import { threadListAdapter } from "./thread-list-adapter";
import { ThreadHydrationProvider } from "./thread-hydration";

const ResetAssistantRuntimeContext = createContext<() => void>(() => {});

export function useResetAssistantRuntime() {
  return useContext(ResetAssistantRuntimeContext);
}

function ThreadMaintenanceProvider({ children }: PropsWithChildren) {
  const runtime = useAssistantRuntime();
  const remoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const messageCount = useAssistantState(({ thread }) => thread.messages.length);

  const [hydratedRemoteId, setHydratedRemoteId] = useState<string | null>(null);
  const [hydrationInFlightRemoteId, setHydrationInFlightRemoteId] = useState<string | null>(null);

  useEffect(() => {
    if (!remoteId || messageCount > 0 || hydratedRemoteId === remoteId) {
      return;
    }

    let cancelled = false;
    setHydrationInFlightRemoteId(remoteId);

    void (async () => {
      try {
        const response = await fetchWithAuth(`/assistant/threads/${remoteId}/state`);
        const persistedState = await jsonOrThrow<AssistantState>(response);
        if (!cancelled) {
          runtime.thread.unstable_loadExternalState(persistedState);
        }
      } catch (error) {
        console.error("Failed to hydrate persisted assistant thread state", error);
      } finally {
        if (!cancelled) {
          setHydratedRemoteId(remoteId);
          setHydrationInFlightRemoteId(null);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [hydratedRemoteId, messageCount, remoteId, runtime.thread]);

  return (
    <ThreadHydrationProvider isHydrating={Boolean(remoteId && hydrationInFlightRemoteId === remoteId)}>
      {children}
    </ThreadHydrationProvider>
  );
}

function ThreadUrlSync() {
  const router = useRouter();
  const pathname = usePathname();
  const remoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const lastRoutedRemoteId = useRef<string | null>(null);

  useEffect(() => {
    const isAssistantRoute = pathname === "/assistant" || pathname.startsWith("/assistant/");
    if (!isAssistantRoute || !remoteId) {
      return;
    }

    const desired = `/assistant/${remoteId}`;
    if (pathname === desired) {
      lastRoutedRemoteId.current = remoteId;
      return;
    }

    if (lastRoutedRemoteId.current === remoteId) {
      return;
    }

    lastRoutedRemoteId.current = remoteId;
    router.replace(desired, { scroll: false });
  }, [pathname, remoteId, router]);

  return null;
}

function useThreadAwareAssistantTransportRuntime() {
  const runtime = useAssistantRuntime();
  const remoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);

  const ensureThreadId = useCallback(async () => {
    if (remoteId) {
      return remoteId;
    }

    const mainThreadId = runtime.threads.getState().mainThreadId;
    const threadListItem = runtime.threads.getItemById(mainThreadId);
    const current = threadListItem.getState();
    if (current.remoteId) {
      return current.remoteId;
    }

    const initialized = await threadListItem.initialize();
    if (initialized.remoteId) {
      return initialized.remoteId;
    }

    throw new Error("Unable to resolve thread id before sending assistant commands");
  }, [remoteId, runtime.threads]);

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
    onError: (error) => {
      console.error("Assistant transport error", error);
    },
  });
}

function RuntimeProviderInstance({ children }: PropsWithChildren) {
  const runtime = useRemoteThreadListRuntime({
    adapter: threadListAdapter,
    runtimeHook: () => useThreadAwareAssistantTransportRuntime(),
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadMaintenanceProvider>
        <ThreadUrlSync />
        {children}
      </ThreadMaintenanceProvider>
    </AssistantRuntimeProvider>
  );
}

export function NoaAssistantRuntimeProvider({ children }: PropsWithChildren) {
  const [epoch, setEpoch] = useState(0);
  const resetRuntime = useCallback(() => {
    setEpoch((current) => current + 1);
  }, []);

  return (
    <ResetAssistantRuntimeContext.Provider value={resetRuntime}>
      <RuntimeProviderInstance key={epoch}>{children}</RuntimeProviderInstance>
    </ResetAssistantRuntimeContext.Provider>
  );
}
