"use client";

import type { PropsWithChildren } from "react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  AssistantRuntimeProvider,
  useRemoteThreadListRuntime,
  useAssistantRuntime,
  useAssistantState,
  useAssistantTransportRuntime,
} from "@assistant-ui/react";

import {
  type AssistantState,
  convertAssistantState,
} from "@/components/lib/assistant-transport-converter";
import { getAuthToken } from "@/components/lib/auth-store";
import { getActiveThreadListItem } from "@/components/lib/assistant-thread-state";
import { fetchWithAuth, getApiUrl, jsonOrThrow } from "@/components/lib/fetch-helper";
import { ThreadHydrationProvider } from "@/components/lib/thread-hydration";
import { threadListAdapter } from "@/components/lib/thread-list-adapter";

const ResetAssistantRuntimeContext = createContext<() => void>(() => {});

function isMissingThreadItemLookupError(error: unknown) {
  return error instanceof Error && error.message.includes("Resource not found for lookup");
}

export function useResetAssistantRuntime() {
  return useContext(ResetAssistantRuntimeContext);
}

function ThreadMaintenanceProvider({ children }: PropsWithChildren) {
  const runtime = useAssistantRuntime();
  const remoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const messageCount = useAssistantState(({ thread }) => thread.messages.length);
  const threadItems = useAssistantState(({ threads }) => threads.threadItems);

  const [hydratedRemoteId, setHydratedRemoteId] = useState<string | null>(null);
  const [hydrationInFlightRemoteId, setHydrationInFlightRemoteId] = useState<string | null>(null);
  const [hydrationCompleted, setHydrationCompleted] = useState<
    | {
        remoteId: string;
        expectsMessages: boolean;
      }
    | null
  >(null);
  const generatedTitles = useRef<Set<string>>(new Set());

  const shouldHydrate = Boolean(remoteId) && messageCount === 0 && hydratedRemoteId !== remoteId;

  const expectsMessages =
    Boolean(remoteId) && hydrationCompleted?.remoteId === remoteId
      ? hydrationCompleted.expectsMessages
      : false;

  const isHydrating = Boolean(remoteId) &&
    messageCount === 0 &&
    (hydrationInFlightRemoteId === remoteId || shouldHydrate || expectsMessages);

  useEffect(() => {
    if (!remoteId) return;
    if (!shouldHydrate) return;

    let cancelled = false;
    setHydrationInFlightRemoteId(remoteId);

    void (async () => {
      let nextExpectsMessages = false;
      try {
        const response = await fetchWithAuth(`/assistant/threads/${remoteId}/state`);
        const persistedState = await jsonOrThrow<AssistantState>(response);
        if (cancelled) return;

        nextExpectsMessages = Array.isArray(persistedState.messages) && persistedState.messages.length > 0;

        runtime.thread.unstable_loadExternalState(persistedState);
      } catch (error) {
        console.error("Failed to hydrate persisted thread state", error);
      } finally {
        if (cancelled) return;
        setHydrationCompleted({ remoteId, expectsMessages: nextExpectsMessages });
        setHydratedRemoteId(remoteId);
        setHydrationInFlightRemoteId(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [remoteId, runtime, shouldHydrate]);

  useEffect(() => {
    if (!remoteId) return;
    if (messageCount > 0) return;
    if (hydratedRemoteId !== remoteId) return;
    if (hydrationInFlightRemoteId === remoteId) return;
    if (!expectsMessages) return;

    const timeout = window.setTimeout(() => {
      setHydrationCompleted((current) => {
        if (!current) return current;
        if (current.remoteId !== remoteId) return current;
        if (!current.expectsMessages) return current;
        return { ...current, expectsMessages: false };
      });
    }, 750);

    return () => {
      window.clearTimeout(timeout);
    };
  }, [expectsMessages, hydratedRemoteId, hydrationInFlightRemoteId, messageCount, remoteId]);

  useEffect(() => {
    let remaining = 3;

    for (const item of threadItems) {
      if (remaining <= 0) break;
      if (!item.remoteId) continue;
      if (item.status !== "regular") continue;
      if (item.title && item.title.trim()) continue;
      if (generatedTitles.current.has(item.id)) continue;

      generatedTitles.current.add(item.id);
      try {
        runtime.threads.getItemById(item.id).generateTitle();
        remaining -= 1;
      } catch (error) {
        if (!isMissingThreadItemLookupError(error)) {
          throw error;
        }
      }
    }
  }, [runtime, threadItems]);

  return <ThreadHydrationProvider isHydrating={isHydrating}>{children}</ThreadHydrationProvider>;
}

function ThreadUrlSync() {
  const router = useRouter();
  const pathname = usePathname();
  const remoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const lastRoutedRemoteId = useRef<string | null>(null);

  useEffect(() => {
    const isAssistantRoute = pathname === "/assistant" || pathname.startsWith("/assistant/");
    if (!isAssistantRoute) return;
    if (!remoteId) return;

    const desired = `/assistant/${remoteId}`;
    if (pathname === desired) {
      lastRoutedRemoteId.current = remoteId;
      return;
    }
    if (lastRoutedRemoteId.current === remoteId) return;
    lastRoutedRemoteId.current = remoteId;

    const timeout = window.setTimeout(() => {
      router.replace(desired, { scroll: false });
    }, 0);

    return () => {
      window.clearTimeout(timeout);
    };
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

    try {
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
    } catch (error) {
      console.warn("Failed to resolve current thread item, switching to a new thread", error);
      await runtime.threads.switchToNewThread();

      const nextMainThreadId = runtime.threads.getState().mainThreadId;
      const nextThreadListItem = runtime.threads.getItemById(nextMainThreadId);
      const initialized = await nextThreadListItem.initialize();
      if (initialized.remoteId) {
        return initialized.remoteId;
      }
    }

    throw new Error("Unable to resolve thread id before sending assistant commands");
  }, [remoteId, runtime]);

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
