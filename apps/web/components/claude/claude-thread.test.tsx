import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockThreadIsEmpty = true;
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

vi.mock("@/components/claude/request-approval-tool-ui", () => ({
  ClaudeToolFallback: () => null,
  ClaudeToolGroup: () => null,
}));

vi.mock("@assistant-ui/react", async () => {
  const React = await import("react");

  const passthrough = ({ children }: { children?: ReactNode }) => <div>{children}</div>;

  return {
    ActionBarPrimitive: {
      Root: passthrough,
      Copy: ({ children, ...props }: React.ComponentPropsWithoutRef<"button">) => (
        <button type="button" {...props}>
          {children}
        </button>
      ),
    },
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
    useAssistantState: (selector: any) => selector({ message: mockAssistantMessage }),
  };
});

import { ClaudeThread } from "./claude-thread";

describe("ClaudeThread", () => {
  beforeEach(() => {
    mockThreadIsEmpty = true;
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
});
