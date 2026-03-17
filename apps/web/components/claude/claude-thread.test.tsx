import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockThreadIsEmpty = true;
let mockThreadListItemStatus: "archived" | "regular" | "new" | "deleted" = "new";
let mockIsHydrating = false;
let mockThreadMessages: any[] = [];
let mockAssistantMessage: any = {
  role: "assistant",
  isLast: true,
  status: { type: "complete", reason: "stop" },
  content: [{ type: "text", text: "" }],
};
const setText = vi.fn();

vi.mock("@/components/lib/auth-store", () => ({
  getAuthUser: vi.fn(() => ({
    id: "1",
    email: "casey@example.com",
    display_name: "Casey",
  })),
}));

vi.mock("@/components/assistant/request-approval-tool-ui", () => ({
  ClaudeToolFallback: () => null,
  ClaudeToolGroup: () => null,
}));

vi.mock("@/components/assistant/workflow-todo-tool-ui", () => ({
  extractLatestCanonicalWorkflowTodos: (messages: any[]) => {
    const last = messages[messages.length - 1];
    return last?.metadata?.custom?.workflow;
  },
  extractLatestWorkflowTodos: (messages: any[]) => {
    const last = messages[messages.length - 1];
    return last?.metadata?.todos ?? [];
  },
}));

vi.mock("@/components/assistant/workflow-dock", () => ({
  WorkflowDock: ({ todos }: { todos: Array<{ content: string }> }) => (
    <div data-testid="workflow-card">{todos.map((todo) => todo.content).join(", ")}</div>
  ),
}));

vi.mock("@/components/lib/thread-hydration", () => ({
  useThreadHydration: () => ({ isHydrating: mockIsHydrating }),
}));

vi.mock("@assistant-ui/react", async () => {
  const React = await import("react");

  const passthrough = ({
    children,
    autoScroll: _autoScroll,
    scrollToBottomOnRunStart: _scrollToBottomOnRunStart,
    scrollToBottomOnInitialize: _scrollToBottomOnInitialize,
    scrollToBottomOnThreadSwitch: _scrollToBottomOnThreadSwitch,
    ...props
  }: React.ComponentPropsWithoutRef<"div"> & {
    children?: ReactNode;
    autoScroll?: boolean;
    scrollToBottomOnRunStart?: boolean;
    scrollToBottomOnInitialize?: boolean;
    scrollToBottomOnThreadSwitch?: boolean;
  }) => <div {...props}>{children}</div>;

  return {
    makeAssistantToolUI: ({ render }: { render: (props: any) => ReactNode }) => render,
    AssistantIf: ({
      children,
      condition,
    }: {
      children?: ReactNode;
      condition?: ({
        thread,
        message,
      }: {
        thread: { isEmpty: boolean };
        message: any;
      }) => boolean;
    }) =>
      condition?.({
        thread: { isEmpty: mockThreadIsEmpty },
        message: mockAssistantMessage,
      }) ?? true ? (
        <>{children}</>
      ) : null,
    ComposerPrimitive: {
      Root: ({ children, className }: { children?: ReactNode; className?: string }) => {
        const testId = className?.includes("max-w-2xl") ? "landing-composer" : "bottom-composer";

        return (
          <form data-testid={testId} className={className}>
            {children}
          </form>
        );
      },
      Input: forwardRef<HTMLTextAreaElement, React.ComponentPropsWithoutRef<"textarea">>(
        ({ ...props }, ref) => <textarea ref={ref} {...props} />,
      ),
      Send: ({ children, ...props }: React.ComponentPropsWithoutRef<"button">) => (
        <button type="submit" {...props}>
          {children}
        </button>
      ),
    },
    MessagePrimitive: {
      Root: passthrough,
      Parts: () => null,
    },
    ThreadPrimitive: {
      Root: passthrough,
      Viewport: passthrough,
      Empty: ({ children }: { children?: ReactNode }) =>
        mockThreadIsEmpty ? <div data-testid="thread-empty">{children}</div> : null,
      Messages: ({ components }: { components: { Message: React.ComponentType } }) =>
        mockThreadIsEmpty ? null : <components.Message />,
    },
    useAssistantApi: () => ({
      composer: () => ({ setText }),
    }),
    useAssistantState: (selector: any) =>
      selector({
        message: mockAssistantMessage,
        thread: {
          isEmpty: mockThreadIsEmpty,
          messages: mockThreadMessages,
        },
        threadListItem: {
          status: mockThreadListItemStatus,
        },
      }),
  };
});

import { ClaudeThread } from "./claude-thread";

describe("ClaudeThread", () => {
  beforeEach(() => {
    mockThreadIsEmpty = true;
    mockThreadListItemStatus = "new";
    mockIsHydrating = false;
    mockThreadMessages = [];
    mockAssistantMessage = {
      role: "assistant",
      isLast: true,
      status: { type: "complete", reason: "stop" },
      content: [{ type: "text", text: "" }],
    };
    setText.mockReset();
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 2, 10, 9, 0));
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("renders the empty-thread landing with a personalized greeting and prompt chips", () => {
    render(<ClaudeThread />);

    expect(screen.getByText(/Morning, Casey/)).toBeInTheDocument();
    expect(screen.getByTestId("landing-composer")).toBeInTheDocument();
    expect(screen.queryByTestId("bottom-composer")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Code" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Claude's choice" })).toBeInTheDocument();
  });

  it("prefills the landing composer and focuses the input when a prompt chip is clicked", async () => {
    render(<ClaudeThread />);

    const input = screen.getByLabelText("Message input");
    fireEvent.click(screen.getByRole("button", { name: "Code" }));

    expect(setText).toHaveBeenCalledWith("Help me write code for...");
    expect(input).toHaveFocus();
  });

  it("shows a loading indicator for a running assistant message before first token", () => {
    mockThreadIsEmpty = false;
    mockAssistantMessage = {
      role: "assistant",
      isLast: true,
      status: { type: "running" },
      content: [{ type: "text", text: "" }],
    };

    render(<ClaudeThread />);

    expect(screen.getByLabelText("Claude is thinking")).toBeInTheDocument();
  });

  it("returns to the standard bottom composer once the thread has messages", () => {
    mockThreadIsEmpty = false;

    render(<ClaudeThread />);

    expect(screen.queryByText(/Morning, Casey/)).not.toBeInTheDocument();
    expect(screen.getByTestId("bottom-composer")).toBeInTheDocument();
    expect(screen.queryByTestId("landing-composer")).not.toBeInTheDocument();
  });

  it("makes the thread viewport the scroll container", () => {
    mockThreadIsEmpty = false;

    render(<ClaudeThread />);

    const viewport = screen.getByTestId("thread-viewport");
    expect(viewport).toHaveClass("min-h-0");
    expect(viewport).toHaveClass("overflow-y-auto");
    expect(viewport).toHaveAttribute("data-auto-scroll", "true");
  });

  it("right-aligns user messages and omits the avatar bubble", () => {
    mockThreadIsEmpty = false;
    mockAssistantMessage = {
      role: "user",
      isLast: true,
      status: { type: "complete", reason: "stop" },
      content: [{ type: "text", text: "Hi" }],
    };

    render(<ClaudeThread />);

    const user = screen.getByTestId("user-message");
    expect(user).toHaveClass("ml-auto");
    expect(screen.queryByText("U")).not.toBeInTheDocument();
  });

  it("shows a skeleton placeholder while hydrating an existing thread", () => {
    mockThreadIsEmpty = true;
    mockThreadListItemStatus = "regular";
    mockIsHydrating = true;

    render(<ClaudeThread />);

    expect(screen.getByLabelText("Loading conversation")).toBeInTheDocument();
    expect(screen.queryByText(/Morning, Casey/)).not.toBeInTheDocument();
  });

  it("pins the canonical workflow dock above the composer", () => {
    mockThreadIsEmpty = false;
    mockThreadMessages = [
      {
        metadata: {
          custom: {
            workflow: [{ content: "Delete user", status: "in_progress", priority: "high" }],
          },
        },
      },
    ];

    render(<ClaudeThread />);

    expect(screen.getByTestId("workflow-card")).toHaveTextContent("Delete user");
  });

  it("falls back to transcript-derived workflow for older threads", () => {
    mockThreadIsEmpty = false;
    mockThreadMessages = [
      {
        metadata: {
          todos: [{ content: "Legacy workflow", status: "pending", priority: "high" }],
        },
      },
    ];

    render(<ClaudeThread />);

    expect(screen.getByTestId("workflow-card")).toHaveTextContent("Legacy workflow");
  });

  it("does not render the assistant disclaimer footer", () => {
    mockThreadIsEmpty = false;

    render(<ClaudeThread />);

    expect(
      screen.queryByText("Claude can make mistakes. Please double-check responses."),
    ).not.toBeInTheDocument();
  });
});
