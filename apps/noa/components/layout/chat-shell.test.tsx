import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  pathname: "/assistant",
  collapsed: "false",
  replaceRoute: vi.fn(),
  switchToNewThread: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
  useRouter: () => ({
    replace: (...args: unknown[]) => mocks.replaceRoute(...args),
  }),
}));

vi.mock("@assistant-ui/react", () => ({
  ThreadListPrimitive: {
    New: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Root: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Items: () => <div data-testid="thread-list-items" />,
  },
  useAssistantApi: () => ({
    threads: () => ({
      switchToNewThread: (...args: unknown[]) => mocks.switchToNewThread(...args),
    }),
  }),
  useAssistantState: () => false,
}));

vi.mock("./chat-sidebar-nav", () => ({ ChatSidebarNav: () => <div>Sidebar nav</div> }));
vi.mock("./chat-thread-item", () => ({ ChatThreadItem: () => <div>Thread row</div> }));
vi.mock("./chat-user-profile", () => ({ ChatUserProfile: () => <div>User footer</div> }));

import { ChatShell } from "./chat-shell";

describe("ChatShell", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mocks.pathname = "/assistant";
    mocks.collapsed = "false";
    mocks.replaceRoute.mockReset();
    mocks.switchToNewThread.mockReset();
    mocks.switchToNewThread.mockResolvedValue(undefined);
  });

  it("renders a viewport-bounded assistant shell", async () => {
    const { container } = render(
      <ChatShell user={null}>
        <div>Thread body</div>
      </ChatShell>,
    );

    expect(container.firstChild).toHaveClass("h-dvh", "overflow-hidden");
  });

  it("keeps recents available in expanded mode", async () => {
    mocks.collapsed = "false";
    window.localStorage.setItem("noa.chat-shell.collapsed", "false");

    render(
      <ChatShell user={null}>
        <div>Thread body</div>
      </ChatShell>,
    );

    expect(await screen.findByText("Recents")).toBeInTheDocument();
    expect(screen.getByTestId("thread-list-items")).toBeInTheDocument();
  });

  it("hides recents in collapsed mode but keeps the expand affordance", async () => {
    mocks.collapsed = "true";
    window.localStorage.setItem("noa.chat-shell.collapsed", "true");

    render(
      <ChatShell user={null}>
        <div>Thread body</div>
      </ChatShell>,
    );

    expect(await screen.findByLabelText("Expand sidebar")).toBeInTheDocument();
    expect(screen.queryByText("Recents")).not.toBeInTheDocument();
  });

  it("switches to a new assistant thread and route on New chat", async () => {
    mocks.switchToNewThread.mockResolvedValue(undefined);

    render(
      <ChatShell user={null}>
        <div>Thread body</div>
      </ChatShell>,
    );

    fireEvent.click(screen.getByLabelText("New chat"));

    await waitFor(() => {
      expect(mocks.switchToNewThread).toHaveBeenCalledTimes(1);
      expect(mocks.replaceRoute).toHaveBeenCalledWith("/assistant", { scroll: false });
    });
  });
});
