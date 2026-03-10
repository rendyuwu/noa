import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useAssistantTransportRuntime = vi.fn(() => ({}));
const unstable_useRemoteThreadListRuntime = vi.fn(
  ({ runtimeHook }: { runtimeHook: () => unknown }) => runtimeHook(),
);

vi.mock("@assistant-ui/react", async () => {
  const React = await import("react");

  return {
    AssistantRuntimeProvider: ({ children }: { children?: ReactNode }) => <>{children}</>,
    unstable_useRemoteThreadListRuntime: (...args: any[]) =>
      unstable_useRemoteThreadListRuntime(...args),
    useAssistantApi: () => ({
      threadListItem: () => ({
        getState: () => ({ remoteId: "thread-1" }),
        initialize: async () => ({ remoteId: "thread-1" }),
      }),
    }),
    useAssistantState: (selector: any) => selector({ threadListItem: { remoteId: "thread-1" } }),
    useAssistantTransportRuntime: (...args: any[]) => useAssistantTransportRuntime(...args),
  };
});

vi.mock("@/components/lib/auth-store", () => ({
  getAuthToken: () => null,
}));

vi.mock("@/components/lib/fetch-helper", () => ({
  getApiUrl: () => "http://example.test",
}));

vi.mock("@/components/lib/thread-list-adapter", () => ({
  threadListAdapter: {},
}));

import { NoaAssistantRuntimeProvider } from "./runtime-provider";

describe("NoaAssistantRuntimeProvider", () => {
  beforeEach(() => {
    useAssistantTransportRuntime.mockClear();
    unstable_useRemoteThreadListRuntime.mockClear();
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
});
