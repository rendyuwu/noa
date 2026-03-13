import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mocks.push,
  }),
}));

vi.mock("@radix-ui/react-dialog", async () => {
  const React = await import("react");

  type DialogState = {
    open: boolean;
    onOpenChange?: (open: boolean) => void;
  };
  type WrapperProps = { children?: React.ReactNode };
  type RootProps = WrapperProps & DialogState;

  const DialogContext = React.createContext<DialogState>({
    open: false,
  });

  return {
    Root: ({ children, open, onOpenChange }: RootProps) => (
      <DialogContext.Provider value={{ open, onOpenChange }}>{children}</DialogContext.Provider>
    ),
    Portal: ({ children }: WrapperProps) => {
      const dialog = React.useContext(DialogContext);
      if (!dialog.open) return null;
      return <div>{children}</div>;
    },
    Overlay: (props: React.ComponentPropsWithoutRef<"div">) => <div {...props} />,
    Content: (props: React.ComponentPropsWithoutRef<"div">) => (
      <div data-testid="dialog-content" {...props} />
    ),
    Title: (props: React.ComponentPropsWithoutRef<"h2">) => <h2 {...props} />,
    Description: (props: React.ComponentPropsWithoutRef<"p">) => <p {...props} />,
    Close: ({ children }: WrapperProps) => <>{children}</>,
  };
});

vi.mock("@/components/claude/claude-thread-list", () => ({
  ClaudeThreadList: ({
    onCloseSidebar,
    onSelectThread,
  }: {
    onCloseSidebar?: () => void;
    onSelectThread?: () => void;
  }) => (
    <div data-testid="sidebar-thread-list">
      <button type="button" onClick={onCloseSidebar}>
        Close sidebar
      </button>
      <button type="button" onClick={onSelectThread}>
        Select thread
      </button>
    </div>
  ),
}));

import { AdminSidebarShell } from "./admin-sidebar-shell";

const DESKTOP_QUERY = "(min-width: 768px)";

type MatchMediaChangeListener = (event: MediaQueryList | MediaQueryListEvent) => void;

function createMatchMediaController(initialDesktopMatch = true) {
  let desktopMatch = initialDesktopMatch;
  const listeners = new Set<MatchMediaChangeListener>();

  const matchMedia = vi.fn((query: string): MediaQueryList => ({
    get matches() {
      return query === DESKTOP_QUERY ? desktopMatch : false;
    },
    media: query,
    onchange: null,
    addEventListener: (eventName: string, listener: EventListenerOrEventListenerObject) => {
      if (eventName !== "change") return;
      listeners.add(listener as MatchMediaChangeListener);
    },
    removeEventListener: (eventName: string, listener: EventListenerOrEventListenerObject) => {
      if (eventName !== "change") return;
      listeners.delete(listener as MatchMediaChangeListener);
    },
    addListener: (listener: MatchMediaChangeListener) => {
      listeners.add(listener);
    },
    removeListener: (listener: MatchMediaChangeListener) => {
      listeners.delete(listener);
    },
    dispatchEvent: () => true,
  }));

  const setDesktopMatch = (matches: boolean) => {
    desktopMatch = matches;
    const event = {
      matches,
      media: DESKTOP_QUERY,
    } as MediaQueryListEvent;
    listeners.forEach((listener) => listener(event));
  };

  return {
    matchMedia,
    setDesktopMatch,
  };
}

describe("AdminSidebarShell", () => {
  let mediaController: ReturnType<typeof createMatchMediaController>;

  beforeEach(() => {
    mocks.push.mockReset();
    mediaController = createMatchMediaController(true);
    vi.stubGlobal("matchMedia", mediaController.matchMedia);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts desktop expanded and shows the thread list", () => {
    const { container } = render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[18rem_minmax(0,1fr)]");
    expect(screen.getByTestId("sidebar-thread-list")).toBeInTheDocument();
  });

  it("collapses desktop sidebar when Close sidebar clicked", () => {
    const { container } = render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[18rem_minmax(0,1fr)]");
    expect(screen.getByTestId("sidebar-thread-list")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close sidebar" }));

    expect(container.firstElementChild).toHaveClass("md:grid-cols-1");
  });

  it("keeps desktop sidebar collapsed after explicit close across viewport changes", () => {
    const { container } = render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Close sidebar" }));
    expect(container.firstElementChild).toHaveClass("md:grid-cols-1");

    mediaController.setDesktopMatch(false);
    act(() => {
      mediaController.setDesktopMatch(true);
    });

    expect(container.firstElementChild).toHaveClass("md:grid-cols-1");
  });

  it("routes to /assistant when a sidebar thread action is selected", () => {
    render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));

    expect(mocks.push).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Select thread" }));

    expect(mocks.push).toHaveBeenCalledTimes(1);
    expect(mocks.push).toHaveBeenCalledWith("/assistant");
  });

  it("keeps the open trigger available on mobile after open, close, and reopen", () => {
    mediaController.setDesktopMatch(false);

    render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));

    const dialog = screen.getByTestId("dialog-content");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close sidebar" }));

    const reopenButton = screen.getByRole("button", { name: "Open sidebar" });
    expect(reopenButton).toBeInTheDocument();

    fireEvent.click(reopenButton);
    expect(screen.getByTestId("dialog-content")).toBeInTheDocument();
  });

  it("shows the open trigger after transitioning from desktop to mobile", () => {
    render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));

    mediaController.setDesktopMatch(false);

    const openButton = screen.getByRole("button", { name: "Open sidebar" });
    expect(openButton).toBeInTheDocument();

    fireEvent.click(openButton);
    expect(screen.getByTestId("dialog-content")).toBeInTheDocument();
  });

  it("opens the desktop sidebar when transitioning from mobile to desktop", () => {
    mediaController.setDesktopMatch(false);

    const { container } = render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    expect(container.firstElementChild).toHaveClass("md:grid-cols-1");

    act(() => {
      mediaController.setDesktopMatch(true);
    });

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[18rem_minmax(0,1fr)]");
    expect(screen.getByTestId("sidebar-thread-list")).toBeInTheDocument();
  });
});
