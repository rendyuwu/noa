import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentType, ReactNode } from "react";
import { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockThreadIsEmpty = true;
let mockThreadIsRunning = false;
let mockThreadListItemStatus: "archived" | "regular" | "new" | "deleted" = "new";
let mockIsHydrating = false;
let mockThreadMessages: any[] = [];
let mockRouteThreadId: string[] | undefined;
let mockAssistantMessage: any = {
  role: "assistant",
  isLast: true,
  status: { type: "complete", reason: "stop" },
  content: [{ type: "text", text: "" }],
};
const setText = vi.fn();
const sendCommand = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({
    threadId: mockRouteThreadId,
  }),
}));

vi.mock("@/components/lib/auth-store", () => ({
  getAuthUser: vi.fn(() => ({
    id: "1",
    email: "casey@example.com",
    display_name: "Casey",
  })),
}));

vi.mock("@/components/assistant-ui/markdown-text", () => ({
  MarkdownText: ({ text }: { text?: string }) => <span>{text}</span>,
}));

vi.mock("@/components/assistant/request-approval-tool-ui", () => ({
  ClaudeToolFallback: () => null,
  ClaudeToolGroup: () => null,
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
        thread: { isEmpty: mockThreadIsEmpty, isRunning: mockThreadIsRunning },
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
      Parts: ({
        components,
      }: {
        components: {
          Reasoning?: ComponentType<any>;
          ReasoningGroup?: ComponentType<any>;
          Text?: ComponentType<any>;
        };
      }) => {
        const Reasoning = components.Reasoning ?? (({ text }: { text?: string }) => <span>{text}</span>);
        const ReasoningGroup = components.ReasoningGroup ?? (({ children }: { children?: ReactNode }) => <>{children}</>);
        const Text = components.Text ?? (({ text }: { text?: string }) => <span>{text}</span>);

        const nodes: ReactNode[] = [];

        for (let index = 0; index < mockAssistantMessage.content.length; index += 1) {
          const part = mockAssistantMessage.content[index];

          if (part.type === "reasoning") {
            const reasoningParts: any[] = [];
            let cursor = index;
            while (cursor < mockAssistantMessage.content.length && mockAssistantMessage.content[cursor].type === "reasoning") {
              reasoningParts.push(mockAssistantMessage.content[cursor]);
              cursor += 1;
            }

            nodes.push(
              <ReasoningGroup key={`reasoning-group-${index}`}>
                {reasoningParts.map((reasoningPart) => (
                  <Reasoning key={`reasoning-${reasoningPart.text ?? "part"}`} {...reasoningPart} />
                ))}
              </ReasoningGroup>,
            );

            index = cursor - 1;
            continue;
          }

          if (part.type === "text") {
            nodes.push(<Text key={`text-${part.text ?? "part"}`} {...part} />);
          }
        }

        return <>{nodes}</>;
      },
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
    useAssistantTransportSendCommand: () => sendCommand,
    useAssistantState: (selector: any) =>
      selector({
        message: mockAssistantMessage,
        thread: {
          isEmpty: mockThreadIsEmpty,
          isRunning: mockThreadIsRunning,
          messages: mockThreadMessages,
        },
        threads: {
          mainThreadId: "thread-local-1",
          threadItems: [
            {
              id: "thread-local-1",
              status: mockThreadListItemStatus,
            },
          ],
        },
      }),
  };
});

import { ClaudeThread } from "./claude-thread";

describe("ClaudeThread", () => {
  beforeEach(() => {
    mockThreadIsEmpty = true;
    mockThreadIsRunning = false;
    mockThreadListItemStatus = "new";
    mockIsHydrating = false;
    mockThreadMessages = [];
    mockRouteThreadId = undefined;
    mockAssistantMessage = {
      role: "assistant",
      isLast: true,
      status: { type: "complete", reason: "stop" },
      content: [{ type: "text", text: "" }],
    };
    setText.mockReset();
    sendCommand.mockReset();
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
    mockThreadIsRunning = true;
    mockAssistantMessage = {
      role: "assistant",
      isLast: true,
      status: { type: "running" },
      content: [{ type: "text", text: "" }],
    };

    render(<ClaudeThread />);

    expect(screen.getByLabelText("Claude is thinking")).toBeInTheDocument();
    expect(screen.getByLabelText("Claude is responding")).toBeInTheDocument();
  });

  it("shows a live run indicator while the active thread is still processing", () => {
    mockThreadIsEmpty = false;
    mockThreadIsRunning = true;

    render(<ClaudeThread />);

    expect(screen.getByLabelText("Claude is responding")).toBeInTheDocument();
  });

  it("shows a streaming indicator once assistant text has started arriving", () => {
    mockThreadIsEmpty = false;
    mockThreadIsRunning = true;
    mockAssistantMessage = {
      role: "assistant",
      isLast: true,
      status: { type: "running" },
      content: [{ type: "text", text: "Drafting an answer" }],
    };

    render(<ClaudeThread />);

    expect(screen.queryByLabelText("Claude is thinking")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Claude is still responding")).toBeInTheDocument();
  });

  it("renders thinking above the answer and keeps it collapsed until opened", () => {
    mockThreadIsEmpty = false;
    mockAssistantMessage = {
      role: "assistant",
      isLast: true,
      status: { type: "complete", reason: "stop" },
      content: [
        {
          type: "reasoning",
          text: Array.from({ length: 20 }, (_, index) => `Line ${index + 1}`).join("\n"),
        },
        {
          type: "text",
          text: "Final answer",
        },
      ],
    };

    render(<ClaudeThread />);

    const thinking = screen.getByRole("button", { name: "Thinking" });
    const answer = screen.getByText("Final answer");

    expect(thinking.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(4);
    expect(thinking).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "Show full" })).not.toBeInTheDocument();

    fireEvent.click(thinking);

    expect(screen.getByRole("button", { name: "Thinking" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByRole("button", { name: "Show full" })).toBeVisible();
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
    expect(viewport).toHaveClass("thread-viewport");
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

  it("does not get stuck on the skeleton once a routed thread is no longer hydrating", () => {
    mockThreadIsEmpty = true;
    mockThreadListItemStatus = "regular";
    mockIsHydrating = false;
    mockRouteThreadId = ["11111111-1111-1111-1111-111111111111"];

    render(<ClaudeThread />);

    expect(screen.queryByLabelText("Loading conversation")).not.toBeInTheDocument();
    expect(screen.getByText(/Morning, Casey/)).toBeInTheDocument();
  });

  it("can still force the skeleton while switching onto a routed thread", () => {
    mockThreadIsEmpty = true;
    mockThreadListItemStatus = "regular";
    mockIsHydrating = false;

    render(<ClaudeThread forceHydrationSkeleton />);

    expect(screen.getByLabelText("Loading conversation")).toBeInTheDocument();
  });

  it("keeps the thread shell focused on messages and composer without workflow chrome", () => {
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

    expect(screen.getByTestId("composer-dock-stack")).toBeInTheDocument();
    expect(screen.queryByTestId("workflow-card")).not.toBeInTheDocument();
  });

  it("does not render the assistant disclaimer footer", () => {
    mockThreadIsEmpty = false;

    render(<ClaudeThread />);

    expect(
      screen.queryByText("Claude can make mistakes. Please double-check responses."),
    ).not.toBeInTheDocument();
  });
});
