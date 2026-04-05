import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  errorMessage: null as string | null,
  isHydrating: false,
  isRunning: false,
  messageCount: 0,
  retry: vi.fn(),
}));

vi.mock("@assistant-ui/react", () => ({
  makeAssistantToolUI: () => () => null,
  ComposerPrimitive: {
    Root: ({ children }: any) => <div>{children}</div>,
    Input: (props: any) => <textarea aria-label="Ask NOA…" {...props} />,
    Send: ({ children }: any) => <div>{children}</div>,
    Cancel: ({ children }: any) => <div>{children}</div>,
  },
  MessagePrimitive: {
    Root: ({ children }: any) => <div>{children}</div>,
    Parts: () => null,
  },
  ThreadPrimitive: {
    Root: ({ children }: any) => <div>{children}</div>,
    Viewport: ({ children }: any) => <div>{children}</div>,
    Empty: ({ children }: any) => <div>{children}</div>,
    Messages: () => <div data-testid="thread-messages" />,
  },
  useAssistantState: (selector: any) =>
    selector({ thread: { messages: Array.from({ length: mocks.messageCount }), isRunning: mocks.isRunning } }),
  useMessage: () => ({ content: [] }),
}));

vi.mock("@/components/lib/runtime/thread-hydration", () => ({
  useThreadHydration: () => ({
    errorMessage: mocks.errorMessage,
    isHydrating: mocks.isHydrating,
    retry: mocks.retry,
  }),
}));

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: any) => <>{children}</>,
  TooltipContent: ({ children }: any) => <>{children}</>,
  TooltipTrigger: ({ children }: any) => <>{children}</>,
}));

vi.mock("./empty-state", () => ({
  EmptyState: () => <div>EMPTY STATE</div>,
}));

import { ThreadPanel } from "./assistant-thread-panel";

describe("ThreadPanel", () => {
  beforeEach(() => {
    mocks.errorMessage = null;
    mocks.isHydrating = false;
    mocks.isRunning = false;
    mocks.messageCount = 0;
    mocks.retry.mockReset();
  });

  it("does not render the empty state while a thread is hydrating", () => {
    mocks.isHydrating = true;

    render(<ThreadPanel />);

    expect(screen.getByText("Restoring conversation…")).toBeInTheDocument();
    expect(screen.queryByText("EMPTY STATE")).not.toBeInTheDocument();
  });
});
