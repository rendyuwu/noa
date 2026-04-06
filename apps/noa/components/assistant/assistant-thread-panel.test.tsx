import { render, screen } from "@testing-library/react";
import { createContext, useContext } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  errorMessage: null as string | null,
  isHydrating: false,
  isRunning: false,
  messageCount: 0,
  retry: vi.fn(),
}));

const ChainOfThoughtScopeContext = createContext(false);

vi.mock("@assistant-ui/react", () => ({
  makeAssistantToolUI: () => () => null,
  ChainOfThoughtPrimitive: {
    Root: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    AccordionTrigger: ({ children, ...props }: any) => {
      if (!useContext(ChainOfThoughtScopeContext)) {
        throw new Error('The current scope does not have a "chainOfThought" property.');
      }

      return <button type="button" {...props}>{children}</button>;
    },
    Parts: ({ components }: any) => (
      <div>
        <components.Reasoning text="Tracing the WHM validation request." />
        <components.tools.Fallback toolName="whm_validate_server" status={{ type: "running" }} />
      </div>
    ),
  },
  ComposerPrimitive: {
    Root: ({ children }: any) => <div>{children}</div>,
    Input: (props: any) => <textarea aria-label="Ask NOA…" {...props} />,
    Send: ({ children, ...props }: any) => <button type="button" {...props}>{children}</button>,
    Cancel: ({ children, ...props }: any) => <button type="button" {...props}>{children}</button>,
  },
  MessagePrimitive: {
    Root: ({ children }: any) => <div>{children}</div>,
    Parts: ({ components }: any) => (
      <div>
        {components?.Text ? <components.Text /> : null}
        {components?.ChainOfThought ? (
          <ChainOfThoughtScopeContext.Provider value={true}>
            <components.ChainOfThought />
          </ChainOfThoughtScopeContext.Provider>
        ) : null}
      </div>
    ),
  },
  ThreadPrimitive: {
    Root: ({ children }: any) => <div>{children}</div>,
    Viewport: ({ children }: any) => <div>{children}</div>,
    Empty: ({ children }: any) => <div>{children}</div>,
    Messages: ({ components }: any) => (
      <div data-testid="thread-messages">
        {components?.AssistantMessage ? <components.AssistantMessage /> : null}
      </div>
    ),
  },
  useAssistantState: (selector: any) =>
    selector({ thread: { messages: Array.from({ length: mocks.messageCount }), isRunning: mocks.isRunning } }),
  useMessage: () => ({ content: [{ type: "text", text: "Hello" }] }),
}));

vi.mock("@assistant-ui/react-markdown", () => ({
  MarkdownTextPrimitive: ({ children }: any) => <div>{children}</div>,
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, ...props }: any) => <button {...props}>{children}</button>,
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

  it("renders chain-of-thought from the scoped message parts path", () => {
    mocks.messageCount = 1;

    render(<ThreadPanel />);

    expect(screen.getByRole("button", { name: /Thinking/ })).toBeInTheDocument();
  });

  it("uses a non-submit send button to avoid double-submit on Enter", () => {
    mocks.messageCount = 1;

    render(<ThreadPanel />);

    expect(screen.getByRole("button", { name: "Send" })).toHaveAttribute("type", "button");
  });
});
