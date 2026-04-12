import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
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

vi.mock("@/components/assistant/claude-thread", () => ({
  ClaudeThread: ({ onOpenSidebarAction }: { onOpenSidebarAction?: () => void }) => (
    <div>
      <button type="button" aria-label="Open sidebar" onClick={onOpenSidebarAction}>
        Open sidebar
      </button>
      <div data-testid="claude-thread" />
    </div>
  ),
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
  return {
    NoaAssistantRuntimeProvider: ({ children }: { children?: ReactNode }) => <>{children}</>,
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
      threads: {
        mainThreadId: "thread-local-1",
        threadItems: [
          {
            id: "thread-local-1",
            remoteId: null,
            status: "new",
          },
        ],
      },
    }),
}));

import AssistantPage from "@/app/(app)/assistant/[[...threadId]]/page";
import AssistantLayout from "@/app/(app)/assistant/layout";
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

  it("renders the assistant route inside a full-bleed layout shell", () => {
    render(
      <AssistantLayout>
        <AssistantPage />
      </AssistantLayout>,
    );

    const main = screen.getByRole("main");
    expect(main).not.toHaveClass("page-shell");
    expect(main).toHaveClass("min-h-dvh");
    expect(main).toHaveClass("bg-background");
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
    const host = thread.parentElement?.parentElement;
    expect(host).not.toBeNull();
    expect(host!).toHaveClass("min-h-0");
    expect(host!).toHaveClass("min-w-0");

    const grid = host!.parentElement;
    expect(grid).not.toBeNull();
    expect(grid!).toHaveClass("min-h-0");
  });

  it("sets the desktop sidebar column to 19rem", () => {
    render(<ClaudeWorkspace />);

    const thread = screen.getByTestId("claude-thread");
    const grid = thread.parentElement?.parentElement?.parentElement;
    expect(grid).not.toBeNull();
    expect(grid!).toHaveClass("md:grid-cols-[19rem_minmax(0,1fr)]");
  });

  it("sets the mobile drawer width to 19rem (capped to 88vw)", () => {
    render(<ClaudeWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));

    const drawer = screen.getByRole("dialog", { name: "Chats" });
    expect(drawer).not.toBeNull();
    expect(drawer!).toHaveClass("w-[19rem]");
    expect(drawer!).toHaveClass("max-w-[88vw]");
    expect(drawer!).toHaveAttribute("data-state", "open");
  });
});
