import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  clearAuth: vi.fn(),
  cancelRun: vi.fn(),
  detachThreadItem: vi.fn(),
  deleteThreadItem: vi.fn(),
  deleteThreadRemote: vi.fn(),
  itemApiById: new Map<string, { delete: ReturnType<typeof vi.fn> }>(),
  push: vi.fn(),
  replace: vi.fn(),
  switchToNewThread: vi.fn(),
  switchToThreadItem: vi.fn(),
  mainThreadId: "thread-a",
  pathname: "/assistant/11111111-1111-1111-1111-111111111111",
  remoteId: "11111111-1111-1111-1111-111111111111",
  threadIds: ["thread-a", "thread-b"],
  threadItems: [
    {
      id: "thread-a",
      remoteId: "11111111-1111-1111-1111-111111111111",
      title: "Hello",
      status: "regular",
    },
    {
      id: "thread-b",
      remoteId: "22222222-2222-2222-2222-222222222222",
      title: "Howdy",
      status: "regular",
    },
  ],
  user: {
    id: "1",
    email: "casey@example.com",
    display_name: "Casey Rivers",
    roles: ["admin"],
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mocks.push,
    replace: mocks.replace,
  }),
  usePathname: () => mocks.pathname,
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
    threads: () => ({
      switchToNewThread: (...args: unknown[]) => mocks.switchToNewThread(...args),
    }),
  }),
  useAssistantRuntime: () => ({
    thread: {
      cancelRun: (...args: unknown[]) => mocks.cancelRun(...args),
      getState: () => ({ isRunning: false }),
    },
    threads: {
      getState: () => ({ mainThreadId: mocks.mainThreadId }),
      getItemById: (id: string) => {
        if (!mocks.itemApiById.has(id)) {
          mocks.itemApiById.set(id, {
            delete: vi.fn((...args: unknown[]) => mocks.deleteThreadItem(id, ...args)),
          });
        }

        const itemApi = mocks.itemApiById.get(id)!;

        return {
          getState: () => ({ id }),
          switchTo: (...args: unknown[]) => mocks.switchToThreadItem(...args),
          unstable_on: () => () => {},
          detach: (...args: unknown[]) => mocks.detachThreadItem(id, ...args),
          delete: (...args: unknown[]) => itemApi.delete(...args),
        };
      },
      switchToNewThread: (...args: unknown[]) => mocks.switchToNewThread(...args),
    },
  }),
  useAssistantState: (selector: any) => selector({
    threadListItem: {
      remoteId: mocks.remoteId,
    },
    threads: {
      mainThreadId: mocks.mainThreadId,
      threadIds: mocks.threadIds,
      threadItems: mocks.threadItems,
    },
  }),
  ThreadListItemByIdProvider: ({ children }: { children?: ReactNode }) => <>{children}</>,
  ThreadListPrimitive: {
    Root: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
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
    mocks.cancelRun.mockReset();
    mocks.detachThreadItem.mockReset();
    mocks.deleteThreadItem.mockReset();
    mocks.deleteThreadRemote.mockReset();
    mocks.push.mockReset();
    mocks.replace.mockReset();
    mocks.mainThreadId = "thread-a";
    mocks.switchToNewThread.mockReset();
    mocks.switchToNewThread.mockImplementation(async () => {
      mocks.mainThreadId = "draft-thread";
      const existing = mocks.threadItems.filter((item) => item.id !== "draft-thread");
      mocks.threadItems = [
        ...existing,
        {
          id: "draft-thread",
          remoteId: null,
          title: undefined,
          status: "new",
        },
      ];
    });
    mocks.switchToThreadItem.mockReset();
    mocks.deleteThreadItem.mockResolvedValue(undefined);
    mocks.deleteThreadRemote.mockResolvedValue(undefined);
    mocks.itemApiById = new Map();
    mocks.pathname = "/assistant/11111111-1111-1111-1111-111111111111";
    mocks.remoteId = "11111111-1111-1111-1111-111111111111";
    mocks.threadIds = ["thread-a", "thread-b"];
    mocks.threadItems = [
      {
        id: "thread-a",
        remoteId: "11111111-1111-1111-1111-111111111111",
        title: "Hello",
        status: "regular",
      },
      {
        id: "thread-b",
        remoteId: "22222222-2222-2222-2222-222222222222",
        title: "Howdy",
        status: "regular",
      },
    ];
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

  it("renders a Backend nav group with backend server links for admin users", () => {
    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    const backendToggle = navSection.getByRole("button", { name: "Backend" });
    expect(backendToggle).toBeInTheDocument();

    expect(navSection.queryByRole("link", { name: "WHM Servers" })).not.toBeInTheDocument();
    expect(navSection.queryByRole("link", { name: "Proxmox Servers" })).not.toBeInTheDocument();
    fireEvent.click(backendToggle);
    expect(navSection.getByRole("link", { name: "WHM Servers" })).toHaveAttribute(
      "href",
      "/admin/whm/servers",
    );
    expect(navSection.getByRole("link", { name: "Proxmox Servers" })).toHaveAttribute(
      "href",
      "/admin/proxmox/servers",
    );
  });

  it("renders a Users nav link under the new chat section for admin users", () => {
    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    expect(navSection.getByRole("link", { name: "Users" })).toHaveAttribute("href", "/admin/users");
    expect(navSection.getByRole("link", { name: "Roles" })).toHaveAttribute("href", "/admin/roles");
  });

  it("marks the active admin route link with aria-current", () => {
    mocks.pathname = "/admin/users";

    render(<ClaudeThreadList />);

    expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Roles" })).not.toHaveAttribute("aria-current");
  });

  it("switches to a fresh draft without explicit navigation from a thread route (ThreadUrlSync handles route)", async () => {
    render(<ClaudeThreadList />);

    fireEvent.click(screen.getByRole("button", { name: "New chat" }));

    await waitFor(() => {
      expect(mocks.switchToNewThread).toHaveBeenCalledTimes(1);
    });

    // No explicit router.push; ThreadUrlSync handles navigation after state settles
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("renders the Users nav link when roles include admin among others", () => {
    mocks.user.roles = ["member", "admin"];

    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    expect(navSection.getByRole("link", { name: "Users" })).toHaveAttribute("href", "/admin/users");
    expect(navSection.getByRole("link", { name: "Roles" })).toHaveAttribute("href", "/admin/roles");
  });

  it("applies active styling to the selected thread row", () => {
    render(<ClaudeThreadList />);

    const trigger = screen.getAllByRole("button", { name: "Untitled" })[0];
    const row = trigger.closest("[data-active]");

    expect(row).not.toBeNull();
    expect(row!).toHaveClass("data-[active]:bg-primary/60");
  });

  it("dedupes recents by remoteId", () => {
    mocks.threadIds = ["thread-a", "thread-b", "thread-a-dup"];
    mocks.threadItems = [
      {
        id: "thread-a",
        remoteId: "11111111-1111-1111-1111-111111111111",
        title: "Hello",
        status: "regular",
      },
      {
        id: "thread-b",
        remoteId: "22222222-2222-2222-2222-222222222222",
        title: "Howdy",
        status: "regular",
      },
      {
        id: "thread-a-dup",
        remoteId: "11111111-1111-1111-1111-111111111111",
        title: "Hello",
        status: "regular",
      },
    ];

    render(<ClaudeThreadList />);

    expect(screen.getAllByRole("button", { name: "Untitled" })).toHaveLength(2);
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

  it("deletes the active thread after switching away (ThreadUrlSync handles route)", async () => {
    render(<ClaudeThreadList />);

    fireEvent.click(screen.getAllByRole("button", { name: "Delete thread" })[0]);

    const confirmDialog = screen.getByRole("dialog", { name: "Delete thread?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete thread" }));

    await waitFor(() => {
      expect(mocks.deleteThreadItem).toHaveBeenCalledTimes(1);
    });

    expect(mocks.cancelRun).not.toHaveBeenCalled();
    expect(mocks.switchToNewThread).toHaveBeenCalledTimes(1);
    // Note: router.replace is no longer called; ThreadUrlSync updates the URL
    // once the new thread initializes with a remoteId.
  });

  it("treats a missing active thread item lookup during delete as already deleted", async () => {
    render(<ClaudeThreadList />);

    fireEvent.click(screen.getAllByRole("button", { name: "Delete thread" })[0]);

    const confirmDialog = screen.getByRole("dialog", { name: "Delete thread?" });
    mocks.switchToNewThread.mockImplementationOnce(async () => {
      mocks.mainThreadId = "draft-thread";
      const existing = mocks.threadItems.filter((item) => item.id !== "draft-thread");
      mocks.threadItems = [
        ...existing,
        {
          id: "draft-thread",
          remoteId: null,
          title: undefined,
          status: "new",
        },
      ];
      mocks.itemApiById.delete("thread-a");
    });
    mocks.deleteThreadItem.mockRejectedValueOnce(new Error("tapLookupResources: Resource not found for lookup"));

    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete thread" }));

    await waitFor(() => {
      expect(mocks.switchToNewThread).toHaveBeenCalledTimes(1);
    });

    // No explicit router.replace; ThreadUrlSync handles it when new thread initializes.
    expect(screen.queryByText("tapLookupResources: Resource not found for lookup")).not.toBeInTheDocument();
  });

  it("hides the Users nav link for non-admin users", () => {
    mocks.user.roles = ["member"];

    render(<ClaudeThreadList />);

    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Roles" })).not.toBeInTheDocument();
  });

  it("hides the Backend nav group for non-admin users", () => {
    mocks.user.roles = ["member"];

    render(<ClaudeThreadList />);

    expect(screen.queryByRole("button", { name: "Backend" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "WHM Servers" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Proxmox Servers" })).not.toBeInTheDocument();
  });

  it("hides the Users nav link when roles are empty or missing", () => {
    mocks.user.roles = [];

    const { unmount } = render(<ClaudeThreadList />);
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Roles" })).not.toBeInTheDocument();
    unmount();

    delete (mocks.user as any).roles;
    render(<ClaudeThreadList />);
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Roles" })).not.toBeInTheDocument();
  });

  it("hides the Users nav link when auth user data is missing", () => {
    mocks.user = null;

    render(<ClaudeThreadList />);

    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Roles" })).not.toBeInTheDocument();
  });

  it("uses a neutral account fallback when auth user data is missing", () => {
    mocks.user = null;

    render(<ClaudeThreadList />);

    expect(screen.getByText("NOA User")).toBeInTheDocument();
    expect(screen.getByText("Signed in")).toBeInTheDocument();
    expect(screen.getByText("N")).toBeInTheDocument();
  });
});
