"use client";

import type { PropsWithChildren } from "react";
import { useCallback } from "react";
import {
  AssistantRuntimeProvider,
  unstable_useRemoteThreadListRuntime as useRemoteThreadListRuntime,
  useAssistantApi,
  useAssistantState,
  useAssistantTransportRuntime,
} from "@assistant-ui/react";

import { getAuthToken } from "@/components/lib/auth-store";
import { getApiUrl } from "@/components/lib/fetch-helper";
import { threadListAdapter } from "@/components/lib/thread-list-adapter";

type AssistantState = {
  messages: Array<{ id?: string; role: string; parts: Array<Record<string, unknown>> }>;
  isRunning: boolean;
};

const converter = (state: AssistantState, connectionMetadata: { pendingCommands: Array<any>; isSending: boolean }) => {
  const optimisticMessages = connectionMetadata.pendingCommands
    .filter((command) => command.type === "add-message")
    .map((command) => command.message);

  return {
    messages: [...(state.messages ?? []), ...optimisticMessages],
    isRunning: Boolean(state.isRunning) || connectionMetadata.isSending,
  };
};

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

    if (current.status === "new") {
      const initialized = await threadListItem.initialize();
      return initialized.remoteId;
    }

    throw new Error("Unable to resolve thread id before sending assistant commands");
  }, [api, remoteId]);

  return useAssistantTransportRuntime({
    api: `${getApiUrl()}/assistant`,
    initialState: {
      messages: [],
      isRunning: false,
    },
    converter,
    body: async () => ({
      threadId: await ensureThreadId(),
    }),
    headers: async () => {
      const token = getAuthToken();
      return token ? { authorization: `Bearer ${token}` } : {};
    },
  });
}

export function NoaAssistantRuntimeProvider({ children }: PropsWithChildren) {
  const runtime = useRemoteThreadListRuntime({
    adapter: threadListAdapter,
    runtimeHook: () => useThreadAwareAssistantTransportRuntime(),
  });

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
