import { render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { normalizeAssistantState } from "@/components/lib/assistant-transport-converter";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
  }),
  usePathname: () => "/assistant",
}));

const useAssistantTransportRuntime = vi.fn(() => ({}));
const useRemoteThreadListRuntime = vi.fn(({ runtimeHook }: { runtimeHook: () => unknown }) => runtimeHook());
const unstable_loadExternalState = vi.fn();
const generateTitle = vi.fn();
const missingLookupItemIds = new Set<string>();
const threadsItem = vi.fn(() => ({ generateTitle }));
const fetchWithAuth = vi.fn();
const jsonOrThrow = vi.fn();

let assistantState: any;

const normalizeHydratedState = (state: any) =>
  normalizeAssistantState({
    ...state,
    messages: Array.isArray(state.messages) ? state.messages : [],
    workflow: Array.isArray(state.workflow) ? state.workflow : [],
    evidenceSections: Array.isArray(state.evidenceSections) ? state.evidenceSections : [],
    pendingApprovals: Array.isArray(state.pendingApprovals) ? state.pendingApprovals : [],
    actionRequests: Array.isArray(state.actionRequests) ? state.actionRequests : [],
    isRunning: Boolean(state.isRunning),
  });

const syncActiveThreadItem = () => {
  assistantState.threads.threadItems = [
    {
      id: assistantState.threads.mainThreadId,
      remoteId: assistantState.threadListItem.remoteId,
      title: assistantState.threadListItem.title,
      status: assistantState.threadListItem.status,
    },
  ];
};

const getCurrentThreadItemRuntime = () => ({
  getState: () => assistantState.threadListItem,
  initialize: async () => ({ remoteId: assistantState.threadListItem.remoteId }),
  generateTitle,
});

const runtime = {
  thread: {
    unstable_loadExternalState,
  },
  threads: {
    getState: () => ({ mainThreadId: assistantState.threads.mainThreadId }),
    getItemById: (...args: any[]) => {
      const [id] = args;
      if (missingLookupItemIds.has(id)) {
        throw new Error("tapLookupResources: Resource not found for lookup");
      }

      if (id === assistantState.threads.mainThreadId) {
        return getCurrentThreadItemRuntime();
      }

      return threadsItem(...args);
    },
  },
};

vi.mock("@assistant-ui/react", async () => {
  const React = await import("react");

  return {
    AssistantRuntimeProvider: ({ children }: { children?: ReactNode }) => <>{children}</>,
    useRemoteThreadListRuntime: (...args: any[]) => useRemoteThreadListRuntime(...args),
    useAssistantRuntime: () => runtime,
    useAssistantState: (selector: any) => selector(assistantState),
    useAssistantTransportRuntime: (...args: any[]) => useAssistantTransportRuntime(...args),
  };
});

vi.mock("@/components/lib/auth-store", () => ({
  clearAuth: vi.fn(),
  isAuthRedirectError: () => false,
  isClearAuthInProgress: () => false,
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
    useRemoteThreadListRuntime.mockClear();
    unstable_loadExternalState.mockClear();
    generateTitle.mockClear();
    threadsItem.mockClear();
    fetchWithAuth.mockReset();
    jsonOrThrow.mockReset();
    missingLookupItemIds.clear();

    assistantState = {
      threadListItem: {
        id: "thread-local-1",
        remoteId: null,
        title: undefined,
        status: "regular",
      },
      thread: {
        messages: [],
      },
      threads: {
        mainThreadId: "thread-local-1",
        threadItems: [],
      },
    };
    syncActiveThreadItem();

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

  it("does not keep the assistant message running after a completed canonical snapshot", () => {
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
            id: "assistant-terminal",
            role: "assistant",
            parts: [{ type: "text", text: "All done" }],
          },
        ],
        isRunning: false,
        runStatus: "COMPLETED",
      },
      { pendingCommands: [], isSending: true },
    );

    expect(result.isRunning).toBe(false);
    expect(result.messages.at(-1)?.status).toEqual({ type: "complete", reason: "stop" });
  });

  it("keeps transport running for a terminal snapshot when a pending add-message command exists", () => {
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
            id: "assistant-terminal",
            role: "assistant",
            parts: [{ type: "text", text: "All done" }],
          },
        ],
        isRunning: false,
        runStatus: "COMPLETED",
      },
      {
        pendingCommands: [
          {
            type: "add-message",
            message: { role: "user", parts: [{ type: "text", text: "One more thing" }] },
          },
        ],
        isSending: true,
      },
    );

    expect(result.isRunning).toBe(true);
    expect(result.messages[0]?.status).toEqual({ type: "running" });
    expect(result.messages.at(-1)?.role).toBe("user");
    expect(result.messages.at(-1)?.content).toEqual([{ type: "text", text: "One more thing" }]);
  });

  it("hydrates persisted thread state into the runtime after remount", async () => {
    assistantState.threadListItem.remoteId = "thread-1";
    syncActiveThreadItem();

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
      expect(unstable_loadExternalState).toHaveBeenCalledWith(normalizeHydratedState(persistedState));
    });
  });

  it("hydrates persisted workflow and approval state without stripping canonical fields", async () => {
    assistantState.threadListItem.remoteId = "thread-2";
    syncActiveThreadItem();

    const persistedState = {
      messages: [
        {
          id: "server-2",
          role: "assistant",
          parts: [{ type: "text", text: "Waiting for approval" }],
        },
      ],
      workflow: [
        {
          content: "Request approval",
          status: "waiting_on_approval",
          priority: "high",
        },
      ],
      pendingApprovals: [
        {
          actionRequestId: "approval-1",
          toolName: "whm_suspend_account",
          risk: "CHANGE",
          arguments: { server_ref: "web2", username: "rendy", reason: "billing hold" },
          status: "PENDING",
        },
      ],
      actionRequests: [
        {
          actionRequestId: "approval-1",
          toolName: "whm_suspend_account",
          risk: "CHANGE",
          arguments: { server_ref: "web2", username: "rendy", reason: "billing hold" },
          status: "PENDING",
          lifecycleStatus: "requested",
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

    render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    await waitFor(() => {
      expect(fetchWithAuth).toHaveBeenCalledWith("/assistant/threads/thread-2/state");
    });

    await waitFor(() => {
      expect(unstable_loadExternalState).toHaveBeenCalledWith(normalizeHydratedState(persistedState));
    });
  });

  it("hydrates cleared canonical workflow and approval state after a previously blocked thread", async () => {
    assistantState.threadListItem.remoteId = "thread-3";
    syncActiveThreadItem();

    const blockedState = {
      messages: [
        {
          id: "server-3",
          role: "assistant",
          parts: [{ type: "text", text: "Waiting for approval" }],
        },
      ],
      workflow: [
        {
          content: "Request approval",
          status: "waiting_on_approval",
          priority: "high",
        },
      ],
      pendingApprovals: [
        {
          actionRequestId: "approval-2",
          toolName: "whm_suspend_account",
          risk: "CHANGE",
          arguments: { server_ref: "web2", username: "rendy", reason: "billing hold" },
          status: "PENDING",
        },
      ],
      actionRequests: [
        {
          actionRequestId: "approval-2",
          toolName: "whm_suspend_account",
          risk: "CHANGE",
          arguments: { server_ref: "web2", username: "rendy", reason: "billing hold" },
          status: "PENDING",
          lifecycleStatus: "requested",
        },
      ],
      isRunning: false,
    };

    const clearedState = {
      messages: [
        {
          id: "server-4",
          role: "assistant",
          parts: [{ type: "text", text: "Done" }],
        },
      ],
      workflow: [],
      pendingApprovals: [],
      actionRequests: [],
      isRunning: false,
    };

    const blockedResponse = new Response(JSON.stringify(blockedState), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });
    const clearedResponse = new Response(JSON.stringify(clearedState), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    fetchWithAuth.mockResolvedValueOnce(blockedResponse);
    jsonOrThrow.mockResolvedValueOnce(blockedState);

    const { unmount } = render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    await waitFor(() => {
      expect(unstable_loadExternalState).toHaveBeenCalledWith(normalizeHydratedState(blockedState));
    });

    unmount();
    fetchWithAuth.mockClear();
    jsonOrThrow.mockClear();
    unstable_loadExternalState.mockClear();

    fetchWithAuth.mockResolvedValueOnce(clearedResponse);
    jsonOrThrow.mockResolvedValueOnce(clearedState);

    render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    await waitFor(() => {
      expect(fetchWithAuth).toHaveBeenCalledWith("/assistant/threads/thread-3/state");
    });

    await waitFor(() => {
      expect(unstable_loadExternalState).toHaveBeenCalledWith(normalizeHydratedState(clearedState));
    });
  });

  it("generates titles for thread list items missing titles", async () => {
    assistantState.threadListItem.remoteId = "thread-1";
    syncActiveThreadItem();

    render(
      <NoaAssistantRuntimeProvider>
        <div />
      </NoaAssistantRuntimeProvider>,
    );

    await waitFor(() => {
      expect(generateTitle).toHaveBeenCalled();
    });

    expect(threadsItem).not.toHaveBeenCalled();
  });

  it("ignores missing thread item lookups while generating titles", async () => {
    assistantState.threadListItem.remoteId = "thread-1";
    assistantState.threads.threadItems = [
      {
        id: "thread-deleted",
        remoteId: "thread-deleted",
        title: undefined,
        status: "regular",
      },
      {
        id: "thread-local-1",
        remoteId: "thread-1",
        title: undefined,
        status: "regular",
      },
    ];
    missingLookupItemIds.add("thread-deleted");

    expect(() => {
      render(
        <NoaAssistantRuntimeProvider>
          <div />
        </NoaAssistantRuntimeProvider>,
      );
    }).not.toThrow();

    await waitFor(() => {
      expect(generateTitle).toHaveBeenCalledTimes(1);
    });
  });
});
