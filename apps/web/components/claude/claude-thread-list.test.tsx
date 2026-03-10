import { fireEvent, render, screen } from "@testing-library/react";
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
  ThreadListPrimitive: {
    Root: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    New: ({ children, ...props }: React.ComponentPropsWithoutRef<"button">) => <button {...props}>{children}</button>,
    Items: () => <div data-testid="thread-items" />,
  },
  ThreadListItemPrimitive: {
    Root: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
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

  it("renders disabled Claude-style nav items under the new chat button", () => {
    render(<ClaudeThreadList />);

    for (const label of ["Search", "Customize", "Projects", "Artifacts", "Code"]) {
      expect(screen.getByRole("button", { name: label })).toBeDisabled();
    }
  });

  it("renders a user footer with avatar initial, name, email, and logout action", () => {
    render(<ClaudeThreadList />);

    expect(screen.getByText("C")).toBeInTheDocument();
    expect(screen.getByText("Casey Rivers")).toBeInTheDocument();
    expect(screen.getByText("casey@example.com")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Admin" })).toHaveAttribute("href", "/admin");

    fireEvent.click(screen.getByRole("button", { name: "Logout" }));
    expect(mocks.clearAuth).toHaveBeenCalledTimes(1);
  });
});
