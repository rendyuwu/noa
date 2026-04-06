import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useThreadHydration } from "./thread-hydration";

const mocks = vi.hoisted(() => ({
  assistantState: {
    threads: {
      mainThreadId: "main-thread",
      threadItems: [] as Array<Record<string, unknown>>,
    },
    thread: {
      messages: [] as Array<unknown>,
      isRunning: false,
      isLoading: false,
    },
  },
  transportConfig: null as any,
  generateTitle: vi.fn(),
  getItemById: vi.fn(),
  initialize: vi.fn(),
  loadExternalState: vi.fn(),
  switchToNewThread: vi.fn(),
  runtime: null as any,
  replaceRoute: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/assistant",
  useRouter: () => ({
    replace: (...args: unknown[]) => mocks.replaceRoute(...args),
  }),
}));

vi.mock("@assistant-ui/react", () => ({
  AssistantRuntimeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  unstable_useRemoteThreadListRuntime: ({ runtimeHook }: { runtimeHook: () => unknown }) => runtimeHook(),
  useAssistantRuntime: () => mocks.runtime,
  useAssistantState: (selector: (state: typeof mocks.assistantState) => unknown) => selector(mocks.assistantState),
  useAssistantTransportRuntime: (config: any) => {
    mocks.transportConfig = config;
    return {};
  },
}));

import { NoaAssistantRuntimeProvider } from "./runtime-provider";

describe("NoaAssistantRuntimeProvider", () => {
  beforeEach(() => {
    mocks.assistantState = {
      threads: {
        mainThreadId: "main-thread",
        threadItems: [],
      },
      thread: {
        messages: [],
        isRunning: false,
        isLoading: false,
      },
    };
    mocks.transportConfig = null;
    mocks.generateTitle.mockReset();
    mocks.getItemById.mockReset();
    mocks.initialize.mockReset();
    mocks.loadExternalState.mockReset();
    mocks.switchToNewThread.mockReset();
    mocks.replaceRoute.mockReset();

    mocks.runtime = {
      thread: {
        unstable_loadExternalState: mocks.loadExternalState,
      },
      threads: {
        getState: () => ({ mainThreadId: "main-thread" }),
        getItemById: mocks.getItemById,
        switchToNewThread: mocks.switchToNewThread,
      },
    };
  });

  it("shares a single thread-id initialization promise across concurrent send attempts", async () => {
    let resolveInitialize: ((value: { remoteId?: string | null }) => void) | undefined;
    const initializePromise = new Promise<{ remoteId?: string | null }>((resolve) => {
      resolveInitialize = resolve;
    });

    const mainThread = {
      getState: () => ({ remoteId: null }),
      initialize: vi.fn(() => initializePromise),
    };

    mocks.getItemById.mockImplementation(() => mainThread);

    render(
      <NoaAssistantRuntimeProvider>
        <div>Child</div>
      </NoaAssistantRuntimeProvider>,
    );

    expect(mocks.transportConfig).toBeTruthy();

    const first = mocks.transportConfig.body();
    const second = mocks.transportConfig.body();

    expect(mainThread.initialize).toHaveBeenCalledTimes(1);
    resolveInitialize?.({ remoteId: "thread-1" });

    await expect(first).resolves.toEqual({ threadId: "thread-1" });
    await expect(second).resolves.toEqual({ threadId: "thread-1" });
  });

  it("exposes the runtime loading state through the hydration provider", () => {
    function HydrationProbe() {
      const hydration = useThreadHydration();
      return <div data-testid="hydrating">{String(hydration.isHydrating)}</div>;
    }

    mocks.assistantState.thread.isLoading = true;

    render(
      <NoaAssistantRuntimeProvider>
        <HydrationProbe />
      </NoaAssistantRuntimeProvider>,
    );

    expect(screen.getByTestId("hydrating")).toHaveTextContent("true");
    expect(mocks.loadExternalState).not.toHaveBeenCalled();
  });

  it("waits for the running turn to finish before syncing the route", async () => {
    mocks.assistantState.threads.threadItems = [
      { id: "main-thread", remoteId: "thread-a", status: "regular", title: "Thread" },
    ];
    mocks.assistantState.thread.messages = [{ id: "m1" }];
    mocks.assistantState.thread.isRunning = true;

    const { rerender } = render(
      <NoaAssistantRuntimeProvider>
        <div>Child</div>
      </NoaAssistantRuntimeProvider>,
    );

    expect(mocks.replaceRoute).not.toHaveBeenCalled();

    mocks.assistantState.thread.isRunning = false;

    rerender(
      <NoaAssistantRuntimeProvider>
        <div>Child</div>
      </NoaAssistantRuntimeProvider>,
    );

    await waitFor(() => {
      expect(mocks.replaceRoute).toHaveBeenCalledWith("/assistant/thread-a", { scroll: false });
    });
  });
});
