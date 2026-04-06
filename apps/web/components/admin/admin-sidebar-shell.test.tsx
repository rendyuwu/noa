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

vi.mock("@/components/assistant/claude-thread-list", () => ({
  ClaudeThreadList: ({
    variant,
    onCloseSidebar,
    onCollapseSidebar,
    onExpandSidebar,
    onSelectThread,
  }: {
    variant?: string;
    onCloseSidebar?: () => void;
    onCollapseSidebar?: () => void;
    onExpandSidebar?: () => void;
    onSelectThread?: () => void;
  }) => (
    <div data-testid="sidebar-thread-list">
      {variant ? <div data-testid="sidebar-variant">{variant}</div> : null}

      {onExpandSidebar ? (
        <button type="button" onClick={onExpandSidebar}>
          Expand sidebar
        </button>
      ) : null}

      {onCollapseSidebar ? (
        <button type="button" onClick={onCollapseSidebar}>
          Collapse sidebar
        </button>
      ) : null}

      {onCloseSidebar ? (
        <button type="button" onClick={onCloseSidebar}>
          Close sidebar
        </button>
      ) : null}

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
    listeners.forEach((listener) => {
      listener(event);
    });
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
    window.localStorage.clear();
    mediaController = createMatchMediaController(true);
    vi.stubGlobal("matchMedia", mediaController.matchMedia);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts desktop collapsed and shows the thread list", () => {
    const { container } = render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[3rem_minmax(0,1fr)]");
    expect(screen.getByTestId("sidebar-thread-list")).toBeInTheDocument();
  });

  it("expands and collapses desktop sidebar", () => {
    const { container } = render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[3rem_minmax(0,1fr)]");

    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));
    expect(container.firstElementChild).toHaveClass("md:grid-cols-[18rem_minmax(0,1fr)]");

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(container.firstElementChild).toHaveClass("md:grid-cols-[3rem_minmax(0,1fr)]");
  });

  it("keeps desktop sidebar collapsed across viewport changes", () => {
    const { container } = render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[3rem_minmax(0,1fr)]");

    mediaController.setDesktopMatch(false);
    act(() => {
      mediaController.setDesktopMatch(true);
    });

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[3rem_minmax(0,1fr)]");
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

    const dialog = screen.getByRole("dialog", { name: "Chats" });
    expect(dialog).toHaveAttribute("data-state", "open");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close sidebar" }));

    const reopenButton = screen.getByRole("button", { name: "Open sidebar" });
    expect(reopenButton).toBeInTheDocument();

    fireEvent.click(reopenButton);
    expect(screen.getByRole("dialog", { name: "Chats" })).toHaveAttribute("data-state", "open");
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
    expect(screen.getByRole("dialog", { name: "Chats" })).toHaveAttribute("data-state", "open");
  });

  it("opens the desktop sidebar when transitioning from mobile to desktop", () => {
    mediaController.setDesktopMatch(false);

    const { container } = render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[3rem_minmax(0,1fr)]");

    act(() => {
      mediaController.setDesktopMatch(true);
    });

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[3rem_minmax(0,1fr)]");
    expect(screen.getByTestId("sidebar-thread-list")).toBeInTheDocument();
  });
});
