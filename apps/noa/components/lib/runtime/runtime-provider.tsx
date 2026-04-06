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
import { fetchWithAuth, getApiUrl, getCsrfToken, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { reportClientError } from "@/components/lib/observability/error-reporting";

import { getActiveThreadListItem } from "./assistant-thread-state";
import { ThreadHydrationProvider } from "./thread-hydration";
import { convertAssistantState } from "./assistant-transport-converter";
import { getThreadRuntimeState } from "./thread-runtime-state";
import { threadListAdapter } from "./thread-list-adapter";

const ResetAssistantRuntimeContext = createContext<() => void>(() => {});

export function useResetAssistantRuntime() {
  return useContext(ResetAssistantRuntimeContext);
}

function ThreadMaintenanceProvider({ children }: PropsWithChildren) {
  const isHydrating = useAssistantState(({ thread }) => Boolean(thread?.isLoading));
  const resetRuntime = useResetAssistantRuntime();

  return (
    <ThreadHydrationProvider
      errorMessage={null}
      isHydrating={isHydrating}
      retry={resetRuntime}
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
  const isRunning = useAssistantState(({ thread }) => Boolean(thread?.isRunning));
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
      isRunning,
    });

    if (!runtimeState.shouldReplaceRoute || !runtimeState.desiredPath) {
      return;
    }

    lastRoutedRemoteId.current = remoteId;
    router.replace(runtimeState.desiredPath, { scroll: false });
  }, [isRunning, messageCount, pathname, remoteId, router]);

  return null;
}

function useThreadAwareAssistantTransportRuntime() {
  const runtime = useAssistantRuntime();
  const remoteId = useAssistantState(({ threads }) => getActiveThreadListItem(threads)?.remoteId ?? null);
  const resolveThreadIdPromiseRef = useRef<Promise<string> | null>(null);

  const ensureThreadId = useCallback(async () => {
    if (remoteId) {
      return remoteId;
    }

    if (resolveThreadIdPromiseRef.current) {
      return resolveThreadIdPromiseRef.current;
    }

    resolveThreadIdPromiseRef.current = (async () => {
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
    })().finally(() => {
      resolveThreadIdPromiseRef.current = null;
    });

    return resolveThreadIdPromiseRef.current;
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
    runtimeHook: useThreadAwareAssistantTransportRuntime,
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
