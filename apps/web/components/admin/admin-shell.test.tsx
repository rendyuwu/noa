import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/admin/admin-nav-sidebar", () => ({
  AdminNavSidebar: ({
    variant,
    onClose,
    onCollapse,
    onExpand,
  }: {
    variant?: string;
    onClose?: () => void;
    onCollapse?: () => void;
    onExpand?: () => void;
  }) => (
    <div data-testid="admin-nav-sidebar">
      {variant ? <div data-testid="sidebar-variant">{variant}</div> : null}

      {onExpand ? (
        <button type="button" onClick={onExpand}>
          Expand sidebar
        </button>
      ) : null}

      {onCollapse ? (
        <button type="button" onClick={onCollapse}>
          Collapse sidebar
        </button>
      ) : null}

      {onClose ? (
        <button type="button" onClick={onClose}>
          Close sidebar
        </button>
      ) : null}
    </div>
  ),
}));

import { AdminShell } from "./admin-shell";

const DESKTOP_QUERY = "(min-width: 768px)";

type MatchMediaChangeListener = NonNullable<Parameters<MediaQueryList["addListener"]>[0]>;

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
    const mediaQueryList = {
      matches,
      media: DESKTOP_QUERY,
    } as MediaQueryList;
    const event = {
      matches,
      media: DESKTOP_QUERY,
    } as MediaQueryListEvent;
    listeners.forEach((listener) => {
      listener.call(mediaQueryList, event);
    });
  };

  return {
    matchMedia,
    setDesktopMatch,
  };
}

describe("AdminShell", () => {
  let mediaController: ReturnType<typeof createMatchMediaController>;

  beforeEach(() => {
    window.localStorage.clear();
    mediaController = createMatchMediaController(true);
    vi.stubGlobal("matchMedia", mediaController.matchMedia);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts desktop expanded and renders the sidebar", () => {
    const { container } = render(
      <AdminShell>
        <div>Admin content</div>
      </AdminShell>,
    );

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[20rem_minmax(0,1fr)]");
    expect(container.querySelector("aside")).not.toHaveClass(
      "border-r",
      "bg-sidebar/95",
      "shadow-[inset_-1px_0_0_var(--ring-warm)]",
    );
    expect(screen.getByTestId("admin-nav-sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-variant")).toHaveTextContent("expanded");
  });

  it("expands and collapses desktop sidebar", () => {
    const { container } = render(
      <AdminShell>
        <div>Admin content</div>
      </AdminShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(container.firstElementChild).toHaveClass("md:grid-cols-[4rem_minmax(0,1fr)]");

    fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(container.firstElementChild).toHaveClass("md:grid-cols-[20rem_minmax(0,1fr)]");
  });

  it("keeps the manual desktop sidebar mode across viewport changes", () => {
    const { container } = render(
      <AdminShell>
        <div>Admin content</div>
      </AdminShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(container.firstElementChild).toHaveClass("md:grid-cols-[4rem_minmax(0,1fr)]");

    mediaController.setDesktopMatch(false);
    act(() => {
      mediaController.setDesktopMatch(true);
    });

    expect(container.firstElementChild).toHaveClass("md:grid-cols-[4rem_minmax(0,1fr)]");
  });

  it("keeps the mobile sheet trigger available after open, close, and reopen", () => {
    mediaController.setDesktopMatch(false);

    const { container } = render(
      <AdminShell>
        <div>Admin content</div>
      </AdminShell>,
    );

    expect(container.querySelector(".pt-14.md\\:pt-0")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Menu" }));

    const dialog = screen.getByRole("dialog", { name: "Admin navigation" });
    expect(dialog).toHaveAttribute("data-state", "open");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close sidebar" }));

    const reopenButton = screen.getByRole("button", { name: "Menu" });
    expect(reopenButton).toBeInTheDocument();

    fireEvent.click(reopenButton);
    expect(screen.getByRole("dialog", { name: "Admin navigation" })).toHaveAttribute("data-state", "open");
  });
});
