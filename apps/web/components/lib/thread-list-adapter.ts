"use client";

import type { unstable_RemoteThreadListAdapter as RemoteThreadListAdapter } from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";

import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

type ApiThread = {
  id: string;
  remoteId: string;
  externalId?: string | null;
  status: "regular" | "archived";
  title?: string | null;
  is_archived: boolean;
};

type ListThreadsResponse = {
  threads: ApiThread[];
};

export const threadListAdapter: RemoteThreadListAdapter = {
  async list() {
    const response = await fetchWithAuth("/threads");
    const body = await jsonOrThrow<ListThreadsResponse>(response);

    return {
      threads: body.threads.map((thread) => ({
        remoteId: thread.remoteId ?? thread.id,
        externalId: thread.externalId ?? undefined,
        status: thread.status,
        title: thread.title ?? undefined,
      })),
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
    return createAssistantStream(async (controller) => {
      const response = await fetchWithAuth(`/threads/${remoteId}/title`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ messages }),
      });
      const payload = await jsonOrThrow<{ title: string }>(response);
      const title = payload.title?.trim() ? payload.title.trim() : "New Thread";
      controller.appendText(title);
    }) as any;
  },
};
