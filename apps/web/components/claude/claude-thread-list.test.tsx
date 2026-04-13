import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createContext, useContext, useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const pathnameListeners = new Set<() => void>();
  let pathnameState = "/assistant/11111111-1111-1111-1111-111111111111";

  return {
    clearAuth: vi.fn(),
    cancelRun: vi.fn(),
    detachThreadItem: vi.fn(),
    deleteThreadItem: vi.fn(),
    deleteThreadRemote: vi.fn(),
    fetchWithAuth: vi.fn(),
    itemApiById: new Map<string, { delete: ReturnType<typeof vi.fn> }>(),
    pathnameListeners,
    get pathname() {
      return pathnameState;
    },
    set pathname(value: string) {
      pathnameState = value;
      for (const listener of pathnameListeners) {
        listener();
      }
    },
    push: vi.fn(),
    replace: vi.fn(),
    resetRuntime: vi.fn(),
    jsonOrThrow: vi.fn(),
    switchToNewThread: vi.fn(),
    switchToThreadItem: vi.fn(),
    mainThreadId: "thread-a",
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
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mocks.push,
    replace: mocks.replace,
  }),
  usePathname: () => {
    const React = require("react") as typeof import("react");

    return React.useSyncExternalStore(
      (onStoreChange: () => void) => {
        mocks.pathnameListeners.add(onStoreChange);
        return () => mocks.pathnameListeners.delete(onStoreChange);
      },
      () => mocks.pathname,
      () => mocks.pathname,
    );
  },
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children?: ReactNode; href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/lib/fetch-helper", () => ({
  fetchWithAuth: (...args: unknown[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: unknown[]) => mocks.jsonOrThrow(...args),
}));

vi.mock("@/components/lib/runtime-provider", () => ({
  useResetAssistantRuntime: () => mocks.resetRuntime,
}));

vi.mock("@/components/ui/dropdown-menu", () => {
  const React = require("react") as typeof import("react");

  type MenuButtonProps = React.ComponentPropsWithoutRef<"button"> & {
    children?: ReactNode;
    asChild?: boolean;
    onSelect?: () => void;
  };

  const DropdownMenuContext = createContext<{
    open: boolean;
    setOpen: (open: boolean) => void;
  } | null>(null);

  function DropdownMenu({ children }: { children?: ReactNode }) {
    const [open, setOpen] = useState(false);

    return <DropdownMenuContext.Provider value={{ open, setOpen }}>{children}</DropdownMenuContext.Provider>;
  }

  function DropdownMenuTrigger({ children, asChild, ...props }: MenuButtonProps) {
    const context = useContext(DropdownMenuContext);

    const handleClick = (event: React.MouseEvent) => {
      context?.setOpen(true);
      props.onClick?.(event as never);
    };

    if (asChild && React.isValidElement(children)) {
      return React.cloneElement(children as React.ReactElement<React.ComponentPropsWithoutRef<"button">>, {
        ...props,
        onClick: handleClick,
      });
    }

    return (
      <button type="button" {...props} onClick={handleClick}>
        {children}
      </button>
    );
  }

  function DropdownMenuContent({ children }: { children?: ReactNode }) {
    const context = useContext(DropdownMenuContext);

    if (!context?.open) {
      return null;
    }

    return <div role="menu">{children}</div>;
  }

  function DropdownMenuItem({ children, onSelect, ...props }: MenuButtonProps) {
    return (
      <button
        type="button"
        role="menuitem"
        {...props}
        onClick={(event) => {
          props.onClick?.(event as never);
          onSelect?.();
        }}
      >
        {children}
      </button>
    );
  }

  function DropdownMenuLabel({ children }: { children?: ReactNode }) {
    return <div>{children}</div>;
  }

  function DropdownMenuRadioGroup({ children }: { children?: ReactNode }) {
    return <div>{children}</div>;
  }

  function DropdownMenuRadioItem({ children }: { children?: ReactNode }) {
    return <button type="button">{children}</button>;
  }

  function DropdownMenuSeparator() {
    return <hr />;
  }

  return {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuRadioGroup,
    DropdownMenuRadioItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
  };
});

vi.mock("@assistant-ui/react", () => {
  const ThreadListItemIndexContext = createContext<number | null>(null);
  type DivProps = import("react").ComponentPropsWithoutRef<"div">;
  type ButtonProps = import("react").ComponentPropsWithoutRef<"button">;

  return {
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
    useAssistantState: (selector: any) =>
      selector({
        threadListItem: {
          remoteId: mocks.remoteId,
        },
        threads: {
          mainThreadId: mocks.mainThreadId,
          threadIds: mocks.threadIds,
          threadItems: mocks.threadItems,
        },
      }),
    ThreadListItemByIndexProvider: ({ children, index }: { children?: ReactNode; index?: number }) => (
      <ThreadListItemIndexContext.Provider value={index ?? null}>{children}</ThreadListItemIndexContext.Provider>
    ),
    ThreadListPrimitive: {
      Root: ({ children, className }: { children?: ReactNode; className?: string }) => (
        <div className={className}>{children}</div>
      ),
    },
    ThreadListItemPrimitive: {
      Root: ({ children, ...props }: DivProps) => (
        <div {...props} data-active="true">
          {children}
        </div>
      ),
      Trigger: ({ children, ...props }: ButtonProps) => <button {...props}>{children}</button>,
      Title: ({ fallback }: { fallback?: string }) => {
        const index = useContext(ThreadListItemIndexContext);
        const title = index == null ? undefined : mocks.threadItems[index]?.title;

        return <span>{title ?? fallback ?? "Untitled"}</span>;
      },
      Delete: ({ children, ...props }: ButtonProps) => <button {...props}>{children}</button>,
    },
  };
});

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
    mocks.fetchWithAuth.mockReset();
    mocks.fetchWithAuth.mockResolvedValue(new Response(null, { status: 204 }));
    mocks.jsonOrThrow.mockReset();
    mocks.push.mockReset();
    mocks.replace.mockReset();
    mocks.resetRuntime.mockReset();
    mocks.pathnameListeners.clear();
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
    expect(screen.getByRole("button", { name: "Account menu" })).toBeInTheDocument();
  });

  it("keeps expanded controls flat and clamps recent titles to one line", () => {
    mocks.threadItems[0] = {
      id: "thread-a",
      remoteId: "11111111-1111-1111-1111-111111111111",
      title: "A very long recent thread title that should stay on one line and never show a tooltip",
      status: "regular",
    };

    render(<ClaudeThreadList onCollapseSidebar={() => {}} />);

    expect(screen.getByRole("button", { name: "New chat" })).not.toHaveClass("bg-card/75", "shadow-sm");
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).not.toHaveClass("rounded-full", "bg-card/70", "shadow-sm");
    expect(screen.getByRole("button", { name: "Account menu" })).not.toHaveClass("bg-card/75", "shadow-sm");

    const titleButton = screen.getByRole("button", {
      name: "A very long recent thread title that should stay on one line and never show a tooltip",
    });
    const title = titleButton.querySelector("span.block");

    expect(title).not.toBeNull();

    expect(title).toHaveClass("truncate");
    expect(title).not.toHaveAttribute("title");
  });

  it("renders the current lightweight chat nav under the new chat button", () => {
    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    expect(navSection.queryByRole("button", { name: "Customize" })).not.toBeInTheDocument();
    expect(navSection.queryByRole("button", { name: "Projects" })).not.toBeInTheDocument();

    const searchButton = navSection.getByRole("button", { name: "Search" });
    expect(searchButton).toHaveAttribute("aria-disabled", "true");
    expect(searchButton).not.toBeDisabled();
    expect(navSection.queryByRole("button", { name: "Artifacts" })).not.toBeInTheDocument();
    expect(navSection.queryByRole("button", { name: "Code" })).not.toBeInTheDocument();
  });

  it("does not render legacy admin nav groups inside the chat sidebar", () => {
    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    expect(navSection.queryByRole("button", { name: "Backend" })).not.toBeInTheDocument();
    expect(navSection.queryByRole("link", { name: "WHM Servers" })).not.toBeInTheDocument();
    expect(navSection.queryByRole("link", { name: "Proxmox Servers" })).not.toBeInTheDocument();
  });

  it("does not render admin links under the new chat section", () => {
    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    expect(navSection.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(navSection.queryByRole("link", { name: "Roles" })).not.toBeInTheDocument();
  });

  it("does not expose legacy admin route highlighting in the chat sidebar", () => {
    mocks.pathname = "/admin/users";

    render(<ClaudeThreadList />);

    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Roles" })).not.toBeInTheDocument();
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

  it("keeps the chat sidebar free of admin links even when roles include admin among others", () => {
    mocks.user.roles = ["member", "admin"];

    render(<ClaudeThreadList />);

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton.parentElement).not.toBeNull();
    const navSection = within(newChatButton.parentElement as HTMLElement);

    expect(navSection.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(navSection.queryByRole("link", { name: "Roles" })).not.toBeInTheDocument();
  });

  it("applies editorial card active styling to the selected thread row", () => {
    render(<ClaudeThreadList />);

    const trigger = screen.getAllByRole("button", { name: "Hello" })[0];
    const row = trigger.closest("[data-active]");

    expect(row).not.toBeNull();
    expect(row!).toHaveClass("data-[active]:bg-card");
    expect(row!).toHaveClass("data-[active]:border-border/70");
    expect(row!).toHaveClass("data-[active]:shadow-sm");
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

    expect(screen.getAllByRole("button", { name: "Hello" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Howdy" })).toHaveLength(1);
  });

  it("renders an admin footer link for admin users in expanded mode", async () => {
    render(<ClaudeThreadList />);

    expect(screen.getByText("C")).toBeInTheDocument();
    expect(screen.getByText("Casey Rivers")).toBeInTheDocument();
    expect(screen.getByText("casey@example.com")).toBeInTheDocument();

    const adminLink = screen.getByRole("link", { name: "Admin" });
    expect(adminLink).toHaveAttribute("href", "/admin");

    await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
    const logoutItem = await screen.findByRole("menuitem", { name: "Log out" });
    logoutItem.focus();
    await userEvent.keyboard("{Enter}");

    const confirmDialog = await screen.findByRole("dialog");
    expect(within(confirmDialog).getByText("Log out?")).toBeInTheDocument();
    expect(mocks.clearAuth).toHaveBeenCalledTimes(0);

    await userEvent.click(within(confirmDialog).getByRole("button", { name: "Log out" }));
    expect(mocks.clearAuth).toHaveBeenCalledTimes(1);
  });

  it("renders an admin rail item for admin users in collapsed mode", () => {
    render(<ClaudeThreadList variant="collapsed" onExpandSidebar={() => {}} />);

    const adminLink = screen.getByRole("link", { name: "Admin" });
    expect(adminLink).toHaveAttribute("href", "/admin");
    expect(screen.getByRole("button", { name: "Account menu" })).toBeInTheDocument();
  });

  it("keeps collapsed rail controls compact enough for the 3rem shell", () => {
    const { container } = render(<ClaudeThreadList variant="collapsed" onExpandSidebar={() => {}} />);

    const rail = container.firstElementChild?.firstElementChild;
    expect(rail).not.toBeNull();
    expect(rail!).toHaveClass("px-1.5");

    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveClass("h-8", "w-8");
    expect(screen.getByRole("button", { name: "New chat" })).toHaveClass("h-8", "w-8");
    expect(screen.getByTitle("Coming soon")).toHaveClass("h-8", "w-8");
    expect(screen.getByRole("button", { name: "Account menu" })).toHaveClass("h-8", "w-8");
  });

  it("keeps the admin entry hidden for non-admin users", () => {
    mocks.user.roles = ["member"];

    render(<ClaudeThreadList />);

    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("keeps the admin entry hidden when there is no signed-in user", () => {
    mocks.user = null as any;

    render(<ClaudeThreadList variant="collapsed" onExpandSidebar={() => {}} />);

    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("uses sidebar semantic background tokens for the sidebar root (expanded)", () => {
    const { container } = render(<ClaudeThreadList />);

    expect(container.firstElementChild).toHaveClass("bg-sidebar");
  });

  it("uses sidebar semantic background tokens for the sidebar root (collapsed)", () => {
    const { container } = render(<ClaudeThreadList variant="collapsed" onExpandSidebar={() => {}} />);

    expect(container.firstElementChild).toHaveClass("bg-sidebar");
  });

  it("deletes the active thread and explicitly routes away from the deleted thread", async () => {
    mocks.replace.mockImplementation(() => {
      mocks.pathname = "/assistant";
    });

    render(<ClaudeThreadList />);

    fireEvent.click(screen.getAllByRole("button", { name: "Delete thread" })[0]);

    const confirmDialog = screen.getByRole("dialog", { name: "Delete thread?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete thread" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith("/threads/11111111-1111-1111-1111-111111111111", {
        method: "DELETE",
      });
      expect(mocks.resetRuntime).toHaveBeenCalledTimes(1);
      expect(mocks.replace).toHaveBeenCalledWith("/assistant", { scroll: false });
    });

    expect(mocks.cancelRun).not.toHaveBeenCalled();
    expect(mocks.switchToNewThread).toHaveBeenCalledTimes(1);
    expect(mocks.deleteThreadItem).not.toHaveBeenCalled();
  });

  it("waits for the route to leave the deleted active thread before sending DELETE", async () => {
    vi.useFakeTimers();

    try {
      mocks.replace.mockImplementation(() => {
        window.setTimeout(() => {
          mocks.pathname = "/assistant";
        }, 50);
      });
      mocks.fetchWithAuth.mockImplementationOnce(async () => {
        expect(mocks.pathname).toBe("/assistant");
        return new Response(null, { status: 204 });
      });

      render(<ClaudeThreadList />);

      fireEvent.click(screen.getAllByRole("button", { name: "Delete thread" })[0]);

      const confirmDialog = screen.getByRole("dialog", { name: "Delete thread?" });
      fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete thread" }));

      await Promise.resolve();

      expect(mocks.replace).toHaveBeenCalledWith("/assistant", { scroll: false });
      expect(mocks.fetchWithAuth).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(49);
      });

      expect(mocks.fetchWithAuth).not.toHaveBeenCalled();
      expect(mocks.pathname).toBe("/assistant/11111111-1111-1111-1111-111111111111");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });

      expect(mocks.pathname).toBe("/assistant");
      expect(mocks.fetchWithAuth).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("deletes a sidebar thread by remoteId through the API and resets assistant runtime", async () => {
    mocks.fetchWithAuth.mockResolvedValue(new Response(null, { status: 204 }));

    render(<ClaudeThreadList />);

    fireEvent.click(screen.getAllByRole("button", { name: "Delete thread" })[1]);

    const confirmDialog = screen.getByRole("dialog", { name: "Delete thread?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete thread" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith("/threads/22222222-2222-2222-2222-222222222222", {
        method: "DELETE",
      });
      expect(mocks.resetRuntime).toHaveBeenCalledTimes(1);
    });

    expect(mocks.detachThreadItem).not.toHaveBeenCalled();
    expect(mocks.deleteThreadItem).not.toHaveBeenCalled();
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
