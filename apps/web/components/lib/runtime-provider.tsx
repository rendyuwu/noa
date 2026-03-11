"use client";

import type { PropsWithChildren } from "react";
import { useCallback, useEffect } from "react";
import {
  AssistantRuntimeProvider,
  unstable_useRemoteThreadListRuntime as useRemoteThreadListRuntime,
  useAssistantApi,
  useAssistantState,
  useAssistantTransportRuntime,
  type ThreadMessage,
} from "@assistant-ui/react";

import { getAuthToken } from "@/components/lib/auth-store";
import { fetchWithAuth, getApiUrl, jsonOrThrow } from "@/components/lib/fetch-helper";
import { threadListAdapter } from "@/components/lib/thread-list-adapter";

type AssistantState = {
  messages: Array<{ id?: string; role: string; parts: Array<Record<string, unknown>> }>;
  isRunning: boolean;
};

const coerceString = (value: unknown): string | undefined => {
  return typeof value === "string" ? value : undefined;
};

const partsToContent = (parts: Array<Record<string, unknown>>) => {
  const content: Array<Record<string, unknown>> = [];

  for (const part of parts) {
    const type = coerceString(part.type);
    if (!type) continue;

    if (type === "text") {
      const text = coerceString(part.text) ?? "";
      content.push({ type: "text", text });
      continue;
    }

    if (type === "image") {
      const image = coerceString(part.image);
      if (image) content.push({ type: "image", image });
      continue;
    }

    if (type === "tool-call") {
      const toolName = coerceString(part.toolName) ?? "unknown";
      const toolCallId = coerceString(part.toolCallId);
      const args = typeof part.args === "object" && part.args !== null ? part.args : undefined;
      const argsText = coerceString(part.argsText);
      content.push({
        type: "tool-call",
        toolName,
        ...(toolCallId ? { toolCallId } : {}),
        ...(args ? { args } : {}),
        ...(argsText ? { argsText } : {}),
      });
      continue;
    }

    // Server persists tool results as standalone "tool" messages.
    // assistant-ui expects tool results to live on a "tool-call" part.
    if (type === "tool-result") {
      const toolName = coerceString(part.toolName) ?? "unknown";
      const toolCallId = coerceString(part.toolCallId);
      const isError = typeof part.isError === "boolean" ? part.isError : undefined;
      content.push({
        type: "tool-call",
        toolName,
        ...(toolCallId ? { toolCallId } : {}),
        argsText: "{}",
        result: part.result,
        ...(isError !== undefined ? { isError } : {}),
      });
      continue;
    }
  }

  return content;
};

const toThreadMessage = (
  raw: { id?: string; role: string; parts: Array<Record<string, unknown>> },
  fallbackId: string,
) : ThreadMessage => {
  const createdAt = new Date();
  const id = raw.id ?? fallbackId;
  const role = raw.role === "tool" ? "assistant" : raw.role;
  const content = partsToContent(raw.parts ?? []);

  if (role === "user") {
    return {
      id,
      createdAt,
      role: "user" as const,
      content: content as any,
      attachments: [],
      metadata: { custom: {} },
    } as ThreadMessage;
  }

  // Treat any non-user role as assistant.
  return {
    id,
    createdAt,
    role: "assistant" as const,
    content: content as any,
    status: { type: "complete", reason: "stop" },
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {},
    },
  } as ThreadMessage;
};

const converter = (
  state: AssistantState,
  connectionMetadata: { pendingCommands: Array<any>; isSending: boolean },
) => {
  const transportIsRunning = Boolean(state.isRunning) || connectionMetadata.isSending;

  const optimisticMessages: ThreadMessage[] = connectionMetadata.pendingCommands
    .filter((command) => command.type === "add-message")
    .map((command, index) =>
      toThreadMessage(
        {
          role: command.message?.role ?? "user",
          parts: command.message?.parts ?? [],
        },
        `optimistic-${index}`,
      ),
    );

  const persistedMessages: ThreadMessage[] = (state.messages ?? []).map((message, index) =>
    toThreadMessage(message, `persisted-${index}`),
  );

  if (transportIsRunning) {
    for (let index = persistedMessages.length - 1; index >= 0; index -= 1) {
      const message = persistedMessages[index];
      if (message?.role !== "assistant") continue;
      persistedMessages[index] = {
        ...message,
        status: { type: "running" },
      };
      break;
    }
  }

  return {
    messages: [...persistedMessages, ...optimisticMessages],
    isRunning: transportIsRunning,
  };
};

function useThreadAwareAssistantTransportRuntime() {
  const api = useAssistantApi();
  const remoteId = useAssistantState(({ threadListItem }) => threadListItem.remoteId);

  useEffect(() => {
    if (!remoteId) return;

    let cancelled = false;

    void (async () => {
      const response = await fetchWithAuth(`/assistant/threads/${remoteId}/state`);
      const persistedState = await jsonOrThrow<AssistantState>(response);
      if (cancelled) return;
      api.thread().unstable_loadExternalState(persistedState);
    })().catch((error) => {
      console.error("Failed to hydrate persisted thread state", error);
    });

    return () => {
      cancelled = true;
    };
  }, [api, remoteId]);

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
    converter,
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

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
