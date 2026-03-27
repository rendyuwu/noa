import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
  }),
  usePathname: () => "/assistant",
  useSearchParams: () => ({
    get: () => null,
  }),
  useParams: () => ({}),
}));

vi.mock("@radix-ui/react-dialog", async () => {
  const React = await import("react");

  type WrapperProps = { children?: React.ReactNode };

  return {
    Root: ({ children }: WrapperProps) => <div>{children}</div>,
    Portal: ({ children }: WrapperProps) => <div>{children}</div>,
    Overlay: (props: React.ComponentPropsWithoutRef<"div">) => <div {...props} />,
    Content: (props: React.ComponentPropsWithoutRef<"div">) => <div {...props} />,
    Title: (props: React.ComponentPropsWithoutRef<"h2">) => <h2 {...props} />,
    Description: (props: React.ComponentPropsWithoutRef<"p">) => <p {...props} />,
    Close: ({ children }: WrapperProps) => <>{children}</>,
  };
});

vi.mock("@/components/assistant/claude-thread", () => ({
  ClaudeThread: () => <div data-testid="claude-thread" />,
}));

vi.mock("@/components/assistant/claude-thread-list", () => ({
  ClaudeThreadList: () => <div data-testid="claude-thread-list" />,
}));

vi.mock("@/components/assistant/request-approval-tool-ui", () => ({
  RequestApprovalToolUI: () => <div data-testid="request-approval-tool-ui" />,
}));

vi.mock("@/components/assistant/workflow-todo-tool-ui", () => ({
  WorkflowTodoToolUI: () => <div data-testid="workflow-todo-tool-ui" />,
}));

vi.mock("@/components/assistant/workflow-receipt-tool-ui", () => ({
  WorkflowReceiptToolUI: () => <div data-testid="workflow-receipt-tool-ui" />,
}));

vi.mock("@/components/lib/auth-store", () => ({
  getAuthUser: () => null,
  useRequireAuth: () => true,
}));

vi.mock("@/components/lib/runtime-provider", async () => {
  const React = await import("react");
  return {
    NoaAssistantRuntimeProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  };
});

vi.mock("@assistant-ui/react", () => ({
  useAssistantApi: () => ({
    threads: () => ({
      switchToThread: async () => {},
      switchToNewThread: async () => {},
    }),
  }),
  useAssistantState: (selector: any) =>
    selector({
      threadListItem: {
        remoteId: null,
        status: "new",
      },
    }),
}));

import AssistantPage from "@/app/(app)/assistant/[[...threadId]]/page";
import { ClaudeWorkspace } from "@/components/assistant/claude-workspace";

describe("/assistant full-bleed shell", () => {
  beforeEach(() => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the assistant route as a full-bleed surface (no page-shell padding)", () => {
    render(<AssistantPage />);

    const main = screen.getByRole("main");
    expect(main).not.toHaveClass("page-shell");
    expect(main).toHaveClass("min-h-dvh");
    expect(main).toHaveClass("bg-bg");
  });

  it("renders ClaudeWorkspace without the framed card shell", () => {
    const { container } = render(<ClaudeWorkspace />);

    const section = container.querySelector("section");
    expect(section).not.toBeNull();

    expect(section!).toHaveClass("h-dvh");
    expect(section!).not.toHaveClass("rounded-2xl");
    expect(section!).not.toHaveClass("border");
    expect(section!.className).not.toMatch(/\bshadow/);
  });

  it("lets the Claude thread column shrink and scroll", () => {
    render(<ClaudeWorkspace />);

    const thread = screen.getByTestId("claude-thread");
    const host = thread.parentElement;
    expect(host).not.toBeNull();
    expect(host!).toHaveClass("min-h-0");
    expect(host!).toHaveClass("min-w-0");

    const grid = host!.parentElement;
    expect(grid).not.toBeNull();
    expect(grid!).toHaveClass("min-h-0");
  });

  it("sets the desktop sidebar column to 18rem", () => {
    render(<ClaudeWorkspace />);

    const thread = screen.getByTestId("claude-thread");
    const grid = thread.parentElement?.parentElement;
    expect(grid).not.toBeNull();
    expect(grid!).toHaveClass("md:grid-cols-[18rem_minmax(0,1fr)]");
  });

  it("sets the mobile drawer width to 18rem (capped to 86vw)", () => {
    render(<ClaudeWorkspace />);

    const drawer = screen.getByText("Chats").parentElement;
    expect(drawer).not.toBeNull();
    expect(drawer!).toHaveClass("w-[18rem]");
    expect(drawer!).toHaveClass("max-w-[86vw]");
  });
});
