"use client";

import type { PropsWithChildren } from "react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  AssistantRuntimeProvider,
  unstable_useRemoteThreadListRuntime as useRemoteThreadListRuntime,
  useAssistantRuntime,
  useAssistantState,
  useAssistantTransportRuntime,
} from "@assistant-ui/react";

import { fetchWithAuth, getApiUrl, getCsrfToken, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { reportClientError } from "@/components/lib/observability/error-reporting";

import { convertAssistantState, type AssistantState } from "./assistant-transport-converter";
import { getActiveThreadListItem } from "./assistant-thread-state";
import { getThreadRuntimeState } from "./thread-runtime-state";
import { ThreadHydrationProvider } from "./thread-hydration";
import { threadListAdapter } from "./thread-list-adapter";

const ResetAssistantRuntimeContext = createContext<() => void>(() => {});

function getHydrationErrorMessage(error: unknown) {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }

  return "We couldn't restore this thread. Retry to reload its last saved state.";
}

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
  const threadItems = useAssistantState(({ threads }) => threads?.threadItems ?? []);

  const [hydratedRemoteId, setHydratedRemoteId] = useState<string | null>(null);
  const [hydrationInFlightRemoteId, setHydrationInFlightRemoteId] = useState<string | null>(null);
  const [hydrationError, setHydrationError] = useState<{ message: string; remoteId: string } | null>(null);
  const [retryVersion, setRetryVersion] = useState(0);
  const attemptedHydration = useRef<{ remoteId: string | null; retryVersion: number }>({
    remoteId: null,
    retryVersion: -1,
  });
  const generatedTitles = useRef<Set<string>>(new Set());
  const runtimeState = getThreadRuntimeState({
    remoteId,
    messageCount,
    hydratedRemoteId,
    hydrationInFlightRemoteId,
    attemptedRemoteId: attemptedHydration.current.remoteId,
    attemptedRetryVersion: attemptedHydration.current.retryVersion,
    retryVersion,
    pathname: "",
    lastRoutedRemoteId: null,
    hasRenderedMessage: messageCount > 0,
  });
  const shouldHydrate = runtimeState.shouldHydrate;
  const isHydrating = runtimeState.isHydrating;

  const retryHydration = useCallback(() => {
    if (!remoteId) {
      return;
    }

    setHydrationError(null);
    setRetryVersion((current) => current + 1);
  }, [remoteId]);

  useEffect(() => {
    if (!remoteId) {
      setHydrationError(null);
      setHydratedRemoteId(null);
      return;
    }

    if (!shouldHydrate) {
      return;
    }

    let cancelled = false;
    attemptedHydration.current = { remoteId, retryVersion };
    setHydrationInFlightRemoteId(remoteId);
    setHydrationError(null);

    void (async () => {
      try {
        const response = await fetchWithAuth(`/assistant/threads/${remoteId}/state`);
        const persistedState = await jsonOrThrow<AssistantState>(response);
        if (cancelled) {
          return;
        }

        runtime.thread.unstable_loadExternalState(persistedState);
        setHydratedRemoteId(remoteId);
      } catch (error) {
        if (cancelled) {
          return;
        }

        reportClientError(error, {
          remoteId,
          source: "assistant.thread-hydration",
        });
        setHydrationError({
          message: getHydrationErrorMessage(error),
          remoteId,
        });
      } finally {
        if (!cancelled) {
          setHydrationInFlightRemoteId(null);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [remoteId, retryVersion, runtime.thread, shouldHydrate]);

  useEffect(() => {
    if (!remoteId || hydrationError?.remoteId !== remoteId || messageCount === 0) {
      return;
    }

    setHydrationError(null);
  }, [hydrationError?.remoteId, messageCount, remoteId]);

  useEffect(() => {
    let remaining = 3;

    for (const item of threadItems) {
      if (remaining <= 0) {
        break;
      }
      if (!item?.remoteId) {
        continue;
      }
      if (item.status !== "regular") {
        continue;
      }
      if (item.title && item.title.trim()) {
        continue;
      }
      if (generatedTitles.current.has(item.id)) {
        continue;
      }

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

  const currentHydrationError = useMemo(() => {
    if (!remoteId || hydrationError === null || hydrationError.remoteId !== remoteId) {
      return null;
    }

    return hydrationError.message;
  }, [hydrationError, remoteId]);

  return (
    <ThreadHydrationProvider
      errorMessage={currentHydrationError}
      isHydrating={isHydrating}
      retry={retryHydration}
    >
      {children}
    </ThreadHydrationProvider>
  );
}

function ThreadUrlSync() {
  const router = useRouter();
  const pathname = usePathname();
  const remoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const messageCount = useAssistantState(({ thread }) => thread.messages.length);
  const lastRoutedRemoteId = useRef<string | null>(null);

  useEffect(() => {
    const runtimeState = getThreadRuntimeState({
      remoteId,
      messageCount,
      hydratedRemoteId: remoteId,
      hydrationInFlightRemoteId: null,
      attemptedRemoteId: null,
      attemptedRetryVersion: -1,
      retryVersion: 0,
      pathname,
      lastRoutedRemoteId: lastRoutedRemoteId.current,
      hasRenderedMessage: messageCount > 0,
    });

    if (!runtimeState.shouldReplaceRoute || !runtimeState.desiredPath) {
      return;
    }

    lastRoutedRemoteId.current = remoteId;
    router.replace(runtimeState.desiredPath, { scroll: false });
  }, [messageCount, pathname, remoteId, router]);

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
      reportClientError(error, {
        source: "assistant.transport.ensure-thread-id",
      });
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
      const csrfToken = getCsrfToken();
      if (!csrfToken) {
        return new Headers();
      }

      return new Headers({ "x-csrf-token": csrfToken });
    },
    onError: (error, { commands }) => {
      reportClientError(error, {
        pendingCommands: commands.length,
        source: "assistant.transport",
      });
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
