import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  activeRemoteId: null as string | null,
  replaceRoute: vi.fn(),
  reportClientError: vi.fn(),
  switchToNewThread: vi.fn(),
  switchToThread: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: (...args: unknown[]) => mocks.replaceRoute(...args),
  }),
}));

vi.mock("@assistant-ui/react", () => ({
  useAssistantApi: () => ({
    threads: () => ({
      switchToNewThread: (...args: unknown[]) => mocks.switchToNewThread(...args),
      switchToThread: (...args: unknown[]) => mocks.switchToThread(...args),
    }),
  }),
  useAssistantState: () => mocks.activeRemoteId,
}));

vi.mock("@/components/lib/observability/error-reporting", () => ({
  reportClientError: (...args: unknown[]) => mocks.reportClientError(...args),
}));

import { RouteThreadSync } from "./assistant-route-thread-sync";

describe("RouteThreadSync", () => {
  beforeEach(() => {
    mocks.activeRemoteId = null;
    mocks.replaceRoute.mockReset();
    mocks.reportClientError.mockReset();
    mocks.switchToNewThread.mockReset();
    mocks.switchToThread.mockReset();
  });

  it("switches to the route thread when it differs from the active thread", async () => {
    mocks.activeRemoteId = "thread-a";
    mocks.switchToThread.mockResolvedValue(undefined);

    render(<RouteThreadSync routeThreadId="thread-b" />);

    await waitFor(() => {
      expect(mocks.switchToThread).toHaveBeenCalledWith("thread-b");
    });

    expect(screen.queryByText("This chat link is invalid or no longer available.")).not.toBeInTheDocument();
  });

  it("surfaces route errors and falls back to a new thread", async () => {
    const error = new Error("thread missing");
    mocks.switchToThread.mockRejectedValue(error);
    mocks.switchToNewThread.mockResolvedValue(undefined);

    render(<RouteThreadSync routeThreadId="missing-thread" />);

    await waitFor(() => {
      expect(mocks.switchToThread).toHaveBeenCalledWith("missing-thread");
    });

    await waitFor(() => {
      expect(mocks.switchToNewThread).toHaveBeenCalled();
    });

    expect(mocks.reportClientError).toHaveBeenCalledWith(error, {
      routeThreadId: "missing-thread",
      source: "assistant.route-thread-sync",
    });
    expect(mocks.replaceRoute).toHaveBeenCalledWith("/assistant", { scroll: false });
    expect(screen.getByText("This chat link is invalid or no longer available.")).toBeInTheDocument();
  });

  it("does not re-open the previous route thread after switching away to a draft thread", async () => {
    mocks.activeRemoteId = "thread-1";
    mocks.switchToThread.mockResolvedValue(undefined);

    const { rerender } = render(<RouteThreadSync routeThreadId="thread-1" />);

    await waitFor(() => {
      expect(mocks.switchToThread).toHaveBeenCalledTimes(0);
    });

    mocks.activeRemoteId = null;
    rerender(<RouteThreadSync routeThreadId="thread-1" />);

    await waitFor(() => {
      expect(mocks.switchToThread).toHaveBeenCalledTimes(0);
    });
  });
});
