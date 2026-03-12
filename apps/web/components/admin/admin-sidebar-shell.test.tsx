import { fireEvent, render, screen } from "@testing-library/react";
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

describe("AdminSidebarShell", () => {
  beforeEach(() => {
    mocks.push.mockReset();
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: true,
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

  it("starts desktop collapsed and shows an open-sidebar button", () => {
    render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    expect(screen.queryByTestId("sidebar-thread-list")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open sidebar" })).toBeInTheDocument();
  });

  it("expands desktop sidebar when Open sidebar clicked", () => {
    render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));

    expect(screen.getByTestId("sidebar-thread-list")).toBeInTheDocument();
  });

  it("routes to /assistant when a sidebar thread action is selected", () => {
    render(
      <AdminSidebarShell>
        <div>Admin content</div>
      </AdminSidebarShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));
    fireEvent.click(screen.getByRole("button", { name: "Select thread" }));

    expect(mocks.push).toHaveBeenCalledWith("/assistant");
  });
});
