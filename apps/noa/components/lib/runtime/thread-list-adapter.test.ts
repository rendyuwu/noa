import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, waitFor } from "@testing-library/react";
import { createElement } from "react";

const fetchWithAuth = vi.fn();
const jsonOrThrow = vi.fn();
const loadExternalState = vi.fn();

const mocks = vi.hoisted(() => ({
  remoteId: "thread-1",
}));

vi.mock("@/components/lib/http/fetch-client", () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuth(...args),
  jsonOrThrow: (...args: unknown[]) => jsonOrThrow(...args),
}));

vi.mock("@assistant-ui/react", () => ({
  ExportedMessageRepository: {
    fromArray: (messages: unknown[]) => ({ messages }),
  },
  RuntimeAdapterProvider: ({ children }: { children: unknown }) => children,
  useAui: () => ({
    thread: () => ({
      __internal_getRuntime: () => ({ unstable_loadExternalState: loadExternalState }),
    }),
  }),
  useAuiState: (selector: any) => selector({ threadListItem: { remoteId: mocks.remoteId } }),
}));

import { createRemoteThreadHistoryAdapter, threadListAdapter } from "./thread-list-adapter";
import { RemoteThreadHistoryProvider } from "./thread-list-adapter";

describe("threadListAdapter", () => {
  beforeEach(() => {
    fetchWithAuth.mockReset();
    jsonOrThrow.mockReset();
    loadExternalState.mockReset();
    mocks.remoteId = "thread-1";
  });

  it("lists threads through the shared auth-aware HTTP client", async () => {
    const response = new Response(null, { status: 200 });
    fetchWithAuth.mockResolvedValue(response);
    jsonOrThrow.mockResolvedValue({
      threads: [
        { id: "1", remoteId: "remote-1", externalId: "local-1", status: "regular", title: "First" },
      ],
    });

    await expect(threadListAdapter.list()).resolves.toEqual({
      threads: [
        {
          remoteId: "remote-1",
          externalId: "local-1",
          status: "regular",
          title: "First",
        },
      ],
    });

    expect(fetchWithAuth).toHaveBeenCalledWith("/threads");
  });

  it("deduplicates repeated remote thread identities", async () => {
    const response = new Response(null, { status: 200 });
    fetchWithAuth.mockResolvedValue(response);
    jsonOrThrow.mockResolvedValue({
      threads: [
        { id: "1", remoteId: "remote-1", externalId: "local-1", status: "regular", title: "First" },
        { id: "2", remoteId: "remote-1", externalId: "local-2", status: "regular", title: "Duplicate" },
      ],
    });

    await expect(threadListAdapter.list()).resolves.toEqual({
      threads: [
        {
          remoteId: "remote-1",
          externalId: "local-1",
          status: "regular",
          title: "First",
        },
      ],
    });
  });

  it("initializes threads with the provided local id", async () => {
    const response = new Response(null, { status: 200 });
    fetchWithAuth.mockResolvedValue(response);
    jsonOrThrow.mockResolvedValue({
      id: "1",
      remoteId: "remote-1",
      externalId: "draft-1",
      status: "regular",
      title: null,
    });

    await expect(threadListAdapter.initialize("draft-1")).resolves.toEqual({
      remoteId: "remote-1",
      externalId: "draft-1",
    });

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/threads",
      expect.objectContaining({
        method: "POST",
      }),
    );
  });

  it("treats delete 404s as already gone", async () => {
    fetchWithAuth.mockResolvedValue(new Response(null, { status: 404 }));

    await expect(threadListAdapter.delete("missing-thread")).resolves.toBeUndefined();
  });

  it("loads persisted assistant state into the thread history repository", async () => {
    fetchWithAuth.mockResolvedValue(new Response(null, { status: 200 }));
    jsonOrThrow.mockResolvedValue({
      messages: [
        {
          id: "m1",
          role: "assistant",
          parts: [{ type: "text", text: "Restored" }],
        },
      ],
      workflow: [],
      pendingApprovals: [],
      actionRequests: [],
      isRunning: false,
    });

    const history = createRemoteThreadHistoryAdapter(() => "thread-1");
    const repo = await history.load();

    expect(repo.messages[0]).toMatchObject({
      role: "assistant",
      content: [{ type: "text", text: "Restored" }],
    });
  });

  it("deduplicates title requests per remote thread", async () => {
    const first = new Response(null, { status: 200 });
    fetchWithAuth.mockResolvedValue(first);
    jsonOrThrow.mockResolvedValue({ title: "Thread Title" });

    const firstStream = await threadListAdapter.generateTitle("remote-1", [] as never[]);
    const secondStream = await threadListAdapter.generateTitle("remote-1", [] as never[]);

    expect(firstStream).toBeDefined();
    expect(secondStream).toBeDefined();
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
  });

  it("loads raw persisted assistant state into the active thread runtime", async () => {
    fetchWithAuth.mockResolvedValue(new Response(null, { status: 200 }));
    jsonOrThrow.mockResolvedValue({
      messages: [
        {
          id: "m1",
          role: "assistant",
          parts: [{ type: "text", text: "Restored" }],
        },
      ],
      workflow: [],
      pendingApprovals: [],
      actionRequests: [],
      isRunning: false,
    });

    render(
      createElement(RemoteThreadHistoryProvider, null, createElement("div", null, "child")),
    );

    await waitFor(() => {
      expect(fetchWithAuth).toHaveBeenCalledWith("/assistant/threads/thread-1/state");
      expect(loadExternalState).toHaveBeenCalledWith(
        expect.objectContaining({
          messages: [
            expect.objectContaining({
              role: "assistant",
              parts: [{ type: "text", text: "Restored" }],
            }),
          ],
        }),
      );
    });
  });
});
