"use client";

import type { PropsWithChildren } from "react";
import {
  AssistantRuntimeProvider,
  unstable_useRemoteThreadListRuntime as useRemoteThreadListRuntime,
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

export function NoaAssistantRuntimeProvider({ children }: PropsWithChildren) {
  const runtime = useRemoteThreadListRuntime({
    adapter: threadListAdapter,
    runtimeHook: () =>
      useAssistantTransportRuntime({
        api: `${getApiUrl()}/assistant`,
        initialState: {
          messages: [],
          isRunning: false,
        },
        converter,
        headers: async () => {
          const token = getAuthToken();
          return token ? { authorization: `Bearer ${token}` } : {};
        },
      }),
  });

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
