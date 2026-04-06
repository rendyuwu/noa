"use client";

import type {
  ThreadHistoryAdapter,
  unstable_RemoteThreadListAdapter as RemoteThreadListAdapter,
} from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";
import { ExportedMessageRepository, RuntimeAdapterProvider, useAui, useAuiState } from "@assistant-ui/react";
import { createElement, type PropsWithChildren, useEffect, useMemo, useRef } from "react";

import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";

import { convertAssistantState, type AssistantState } from "./assistant-transport-converter";

type ApiThread = {
  id: string;
  remoteId: string;
  externalId?: string | null;
  status: "regular" | "archived";
  title?: string | null;
};

type ListThreadsResponse = {
  threads: ApiThread[];
};

export function convertPersistedAssistantState(persistedState: AssistantState) {
  return convertAssistantState(persistedState, { pendingCommands: [], isSending: false });
}

export function createRemoteThreadHistoryAdapter(getRemoteId: () => string | null): ThreadHistoryAdapter {
  return {
    async load() {
      const remoteId = getRemoteId();
      if (!remoteId) {
        return { messages: [] };
      }

      const response = await fetchWithAuth(`/assistant/threads/${remoteId}/state`);
      const body = await jsonOrThrow<AssistantState>(response);
      const converted = convertPersistedAssistantState(body);

      return ExportedMessageRepository.fromArray(converted.messages);
    },
    async append() {
      return;
    },
  };
}

export function RemoteThreadHistoryProvider({ children }: PropsWithChildren) {
  const aui = useAui();
  const remoteId = useAuiState((state) => state.threadListItem.remoteId ?? null);
  const loadedRemoteIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!remoteId || loadedRemoteIdRef.current === remoteId) {
      return;
    }

    let cancelled = false;

    void (async () => {
      const response = await fetchWithAuth(`/assistant/threads/${remoteId}/state`);
      const rawState = await jsonOrThrow<AssistantState>(response);

      if (cancelled) {
        return;
      }

      const threadRuntime = aui.thread().__internal_getRuntime?.() as { unstable_loadExternalState?: (state: AssistantState) => void } | null;
      threadRuntime?.unstable_loadExternalState?.(rawState);
      loadedRemoteIdRef.current = remoteId;
    })();

    return () => {
      cancelled = true;
    };
  }, [aui, remoteId]);

  const adapters = useMemo(() => ({ history: createRemoteThreadHistoryAdapter(() => remoteId) }), [remoteId]);

  return createElement(RuntimeAdapterProvider, { adapters, children });
}

const generatedTitleThreads = new Set<string>();

export const threadListAdapter: RemoteThreadListAdapter = {
  async list() {
    const response = await fetchWithAuth("/threads");
    const body = await jsonOrThrow<ListThreadsResponse>(response);
    const seenRemoteIds = new Set<string>();
    const threads: Array<{
      remoteId: string;
      externalId?: string;
      status: "regular" | "archived";
      title?: string;
    }> = [];

    for (const thread of body.threads) {
      const remoteId = thread.remoteId ?? thread.id;
      if (seenRemoteIds.has(remoteId)) {
        continue;
      }

      seenRemoteIds.add(remoteId);
      threads.push({
        remoteId,
        externalId: thread.externalId ?? undefined,
        status: thread.status,
        title: thread.title ?? undefined,
      });
    }

    return {
      threads,
    };
  },

  async initialize(localId) {
    const response = await fetchWithAuth("/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ localId }),
    });
    const body = await jsonOrThrow<ApiThread>(response);

    return {
      remoteId: body.remoteId ?? body.id,
      externalId: body.externalId ?? undefined,
    };
  },

  async rename(remoteId, title) {
    const response = await fetchWithAuth(`/threads/${remoteId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title }),
    });
    await jsonOrThrow(response);
  },

  async archive(remoteId) {
    const response = await fetchWithAuth(`/threads/${remoteId}/archive`, { method: "POST" });
    await jsonOrThrow(response);
  },

  async unarchive(remoteId) {
    const response = await fetchWithAuth(`/threads/${remoteId}/unarchive`, { method: "POST" });
    await jsonOrThrow(response);
  },

  async delete(remoteId) {
    const response = await fetchWithAuth(`/threads/${remoteId}`, { method: "DELETE" });
    if (response.status === 204 || response.status === 404) {
      return;
    }

    if (!response.ok) {
      await jsonOrThrow(response);
    }
  },

  async fetch(remoteId) {
    const response = await fetchWithAuth(`/threads/${remoteId}`);
    const body = await jsonOrThrow<ApiThread>(response);

    return {
      remoteId: body.remoteId ?? body.id,
      externalId: body.externalId ?? undefined,
      status: body.status,
      title: body.title ?? undefined,
    };
  },

  async generateTitle(remoteId, messages) {
    if (generatedTitleThreads.has(remoteId)) {
      return createAssistantStream(async () => {}) as never;
    }

    generatedTitleThreads.add(remoteId);

    return createAssistantStream(async (controller) => {
      try {
        const response = await fetchWithAuth(`/threads/${remoteId}/title`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ messages }),
        });
        const payload = await jsonOrThrow<{ title: string }>(response);
        const title = payload.title?.trim() ? payload.title.trim() : "New Thread";
        controller.appendText(title);
      } catch (error) {
        generatedTitleThreads.delete(remoteId);
        throw error;
      }
    }) as never;
  },

  unstable_Provider: ({ children }: PropsWithChildren) => {
    return createElement(RemoteThreadHistoryProvider, null, children);
  },
};
