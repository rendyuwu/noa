import { render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useAssistantTransportRuntime = vi.fn(() => ({}));
const unstable_useRemoteThreadListRuntime = vi.fn(
  ({ runtimeHook }: { runtimeHook: () => unknown }) => runtimeHook(),
);
const unstable_loadExternalState = vi.fn();
const generateTitle = vi.fn();
const threadsItem = vi.fn(() => ({ generateTitle }));
const fetchWithAuth = vi.fn();
const jsonOrThrow = vi.fn();

let assistantState: any;

const runtime = {
  thread: {
    unstable_loadExternalState,
  },
};

const api = {
  threadListItem: () => ({
    getState: () => assistantState.threadListItem,
    initialize: async () => ({ remoteId: assistantState.threadListItem.remoteId }),
  }),
  threads: () => ({
    item: (...args: any[]) => threadsItem(...args),
  }),
};

vi.mock("@assistant-ui/react", async () => {
  const React = await import("react");

  return {
    AssistantRuntimeProvider: ({ children }: { children?: ReactNode }) => <>{children}</>,
    unstable_useRemoteThreadListRuntime: (...args: any[]) =>
      unstable_useRemoteThreadListRuntime(...args),
    useAssistantApi: () => api,
    useAssistantRuntime: () => runtime,
    useAssistantState: (selector: any) => selector(assistantState),
    useAssistantTransportRuntime: (...args: any[]) => useAssistantTransportRuntime(...args),
  };
});

vi.mock("@/components/lib/auth-store", () => ({
  getAuthToken: () => null,
}));

vi.mock("@/components/lib/fetch-helper", () => ({
  getApiUrl: () => "http://example.test",
  fetchWithAuth: (...args: any[]) => fetchWithAuth(...args),
  jsonOrThrow: (...args: any[]) => jsonOrThrow(...args),
}));

vi.mock("@/components/lib/thread-list-adapter", () => ({
  threadListAdapter: {},
}));

import { NoaAssistantRuntimeProvider } from "./runtime-provider";

describe("NoaAssistantRuntimeProvider", () => {
  beforeEach(() => {
    useAssistantTransportRuntime.mockClear();
    unstable_useRemoteThreadListRuntime.mockClear();
    unstable_loadExternalState.mockClear();
    generateTitle.mockClear();
    threadsItem.mockClear();
    fetchWithAuth.mockReset();
    jsonOrThrow.mockReset();

    assistantState = {
      threadListItem: {
        id: "thread-local-1",
        remoteId: "thread-1",
        title: undefined,
        status: "regular",
      },
      thread: {
        messages: [],
      },
      threads: {
        threadItems: [],
      },
    };

    const emptyState = { messages: [], isRunning: false };
    const response = new Response(JSON.stringify(emptyState), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });
    fetchWithAuth.mockResolvedValue(response);
    jsonOrThrow.mockResolvedValue(emptyState);
  });

  it("passes assistant-transport protocol to the runtime", () => {
    render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    expect(useAssistantTransportRuntime).toHaveBeenCalledWith(
      expect.objectContaining({ protocol: "assistant-transport" }),
    );
  });

  it("marks the last assistant message as running while state.isRunning is true", () => {
    render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    const options = useAssistantTransportRuntime.mock.calls.at(-1)?.[0] as any;
    const result = options.converter(
      {
        messages: [
          {
            id: "assistant-streaming",
            role: "assistant",
            parts: [{ type: "text", text: "" }],
          },
        ],
        isRunning: true,
      },
      { pendingCommands: [], isSending: false },
    );

    expect(result.messages.at(-1)?.status).toEqual({ type: "running" });
  });

  it("still appends optimistic user messages for pending add-message commands", () => {
    render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    const options = useAssistantTransportRuntime.mock.calls.at(-1)?.[0] as any;
    const result = options.converter(
      {
        messages: [],
        isRunning: false,
      },
      {
        pendingCommands: [
          {
            type: "add-message",
            message: { role: "user", parts: [{ type: "text", text: "Hi" }] },
          },
        ],
        isSending: true,
      },
    );

    expect(result.messages.at(-1)?.role).toBe("user");
    expect(result.messages.at(-1)?.content).toEqual([{ type: "text", text: "Hi" }]);
  });

  it("hydrates persisted thread state into the runtime after remount", async () => {
    const persistedState = {
      messages: [
        {
          id: "server-1",
          role: "assistant",
          parts: [{ type: "text", text: "Hello" }],
        },
      ],
      isRunning: false,
    };

    const response = new Response(JSON.stringify(persistedState), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });
    fetchWithAuth.mockResolvedValue(response);
    jsonOrThrow.mockResolvedValue(persistedState);

    const { unmount } = render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    unmount();
    fetchWithAuth.mockClear();
    jsonOrThrow.mockClear();
    unstable_loadExternalState.mockClear();

    render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    await waitFor(() => {
      expect(fetchWithAuth).toHaveBeenCalledWith("/assistant/threads/thread-1/state");
    });

    await waitFor(() => {
      expect(jsonOrThrow).toHaveBeenCalledWith(response);
    });

    await waitFor(() => {
      expect(unstable_loadExternalState).toHaveBeenCalledWith(persistedState);
    });
  });

  it("generates titles for thread list items missing titles", async () => {
    assistantState.threads.threadItems = [
      {
        id: "thread-local-1",
        remoteId: "thread-1",
        title: undefined,
        status: "regular",
      },
    ];

    render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    await waitFor(() => {
      expect(threadsItem).toHaveBeenCalledWith({ id: "thread-local-1" });
    });

    await waitFor(() => {
      expect(generateTitle).toHaveBeenCalled();
    });
  });
});
