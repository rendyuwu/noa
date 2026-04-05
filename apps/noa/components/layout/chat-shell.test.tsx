import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  pathname: "/assistant",
  collapsed: "false",
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
}));

vi.mock("@assistant-ui/react", () => ({
  ThreadListPrimitive: {
    New: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Root: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Items: () => <div data-testid="thread-list-items" />,
  },
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
});
