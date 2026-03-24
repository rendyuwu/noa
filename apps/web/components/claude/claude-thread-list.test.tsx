import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  clearAuth: vi.fn(),
  user: {
    id: "1",
    email: "casey@example.com",
    display_name: "Casey Rivers",
    roles: ["admin"],
  },
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children?: ReactNode; href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@assistant-ui/react", () => ({
  useAssistantApi: () => ({
    threadListItem: () => ({
      delete: () => {},
    }),
  }),
  ThreadListPrimitive: {
    Root: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    New: ({ children, ...props }: React.ComponentPropsWithoutRef<"button">) => <button {...props}>{children}</button>,
    Items: ({ components }: { components?: { ThreadListItem?: (props: any) => ReactNode } }) => {
      const ThreadListItem = components?.ThreadListItem;

      return <div data-testid="thread-items">{ThreadListItem ? <ThreadListItem /> : null}</div>;
    },
  },
  ThreadListItemPrimitive: {
    Root: ({ children, ...props }: React.ComponentPropsWithoutRef<"div">) => (
      <div {...props} data-active="true">
        {children}
      </div>
    ),
    Trigger: ({ children, ...props }: React.ComponentPropsWithoutRef<"button">) => <button {...props}>{children}</button>,
    Title: ({ fallback }: { fallback?: string }) => <span>{fallback ?? "Untitled"}</span>,
    Delete: ({ children, ...props }: React.ComponentPropsWithoutRef<"button">) => <button {...props}>{children}</button>,
  },
}));

vi.mock("@/components/lib/auth-store", () => ({
  clearAuth: mocks.clearAuth,
  getAuthUser: () => mocks.user,
}));

import { ClaudeThreadList } from "./claude-thread-list";

describe("ClaudeThreadList", () => {
  beforeEach(() => {
    mocks.clearAuth.mockReset();
    mocks.user = {
      id: "1",
      email: "casey@example.com",
      display_name: "Casey Rivers",
      roles: ["admin"],
    };
  });

  it("renders a Claude-inspired NOA sidebar with a recents label and cleaner account actions", () => {
    render(<ClaudeThreadList />);

    expect(screen.getByText("Recents")).toBeInTheDocument();

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton).toBeInTheDocument();
    expect(newChatButton).toHaveClass("px-4");

    expect(screen.getByRole("button", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Logout" })).toBeInTheDocument();
  });

  it("renders disabled Claude-style nav items under the new chat button", () => {
    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    expect(navSection.queryByRole("button", { name: "Customize" })).not.toBeInTheDocument();

    expect(navSection.queryByRole("button", { name: "Projects" })).not.toBeInTheDocument();

    for (const label of ["Search", "Artifacts", "Code"]) {
      const button = navSection.getByRole("button", { name: label });
      expect(button).toHaveAttribute("aria-disabled", "true");
      expect(button).not.toBeDisabled();
    }
  });

  it("renders a Backend nav group with a WHM Servers link for admin users", () => {
    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    const backendToggle = navSection.getByRole("button", { name: "Backend" });
    expect(backendToggle).toBeInTheDocument();

    expect(navSection.queryByRole("link", { name: "WHM Servers" })).not.toBeInTheDocument();
    fireEvent.click(backendToggle);
    expect(navSection.getByRole("link", { name: "WHM Servers" })).toHaveAttribute(
      "href",
      "/admin/whm/servers",
    );
  });

  it("renders a Users nav link under the new chat section for admin users", () => {
    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    expect(navSection.getByRole("link", { name: "Users" })).toHaveAttribute("href", "/admin/users");
  });

  it("renders the Users nav link when roles include admin among others", () => {
    mocks.user.roles = ["member", "admin"];

    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    expect(navSection.getByRole("link", { name: "Users" })).toHaveAttribute("href", "/admin/users");
  });

  it("applies active styling to the selected thread row", () => {
    render(<ClaudeThreadList />);

    const trigger = screen.getByRole("button", { name: "Untitled" });
    const row = trigger.closest("[data-active]");

    expect(row).not.toBeNull();
    expect(row!).toHaveClass("data-[active]:bg-surface-2/60");
  });

  it("renders a user footer with avatar initial, name, email, and logout action", () => {
    render(<ClaudeThreadList />);

    expect(screen.getByText("C")).toBeInTheDocument();
    expect(screen.getByText("Casey Rivers")).toBeInTheDocument();
    expect(screen.getByText("casey@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Logout" }));
    expect(mocks.clearAuth).toHaveBeenCalledTimes(0);

    const confirmDialog = screen.getByRole("dialog", { name: "Log out?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Log out" }));
    expect(mocks.clearAuth).toHaveBeenCalledTimes(1);
  });

  it("hides the Users nav link for non-admin users", () => {
    mocks.user.roles = ["member"];

    render(<ClaudeThreadList />);

    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });

  it("hides the Backend nav group for non-admin users", () => {
    mocks.user.roles = ["member"];

    render(<ClaudeThreadList />);

    expect(screen.queryByRole("button", { name: "Backend" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "WHM Servers" })).not.toBeInTheDocument();
  });

  it("hides the Users nav link when roles are empty or missing", () => {
    mocks.user.roles = [];

    const { unmount } = render(<ClaudeThreadList />);
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    unmount();

    delete (mocks.user as any).roles;
    render(<ClaudeThreadList />);
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });

  it("hides the Users nav link when auth user data is missing", () => {
    mocks.user = null;

    render(<ClaudeThreadList />);

    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });

  it("uses a neutral account fallback when auth user data is missing", () => {
    mocks.user = null;

    render(<ClaudeThreadList />);

    expect(screen.getByText("NOA User")).toBeInTheDocument();
    expect(screen.getByText("Signed in")).toBeInTheDocument();
    expect(screen.getByText("N")).toBeInTheDocument();
  });
});
