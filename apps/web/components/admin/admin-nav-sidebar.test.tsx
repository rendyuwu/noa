import type { ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  pathname: "/admin/users",
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
}));

vi.mock("@/components/lib/auth-store", () => ({
  clearAuth: vi.fn(),
  getAuthUser: () => ({
    id: "1",
    email: "admin@example.com",
    roles: ["admin"],
  }),
}));

vi.mock("@/components/noa/account-menu", () => ({
  AccountMenu: ({ trigger }: { trigger: ReactNode }) => <div>{trigger}</div>,
}));

import { AdminNavSidebar } from "./admin-nav-sidebar";

describe("AdminNavSidebar", () => {
  beforeEach(() => {
    mocks.pathname = "/admin/users";
  });

  it("renders the serif NOA heading, section labels, and active nav state", () => {
    const { container } = render(<AdminNavSidebar />);

    const nav = screen.getByRole("navigation");

    expect(screen.getByText("NOA")).toHaveClass("font-serif");
    expect(nav).toHaveClass("bg-sidebar/95");
    expect(nav).toHaveClass("border-sidebar-border/80");
    expect(screen.getByText("Infrastructure")).toBeInTheDocument();
    expect(within(nav).getAllByText("Admin").some((element) => element.tagName === "DIV")).toBe(true);

    const links = within(nav).getAllByRole("link");
    const linkNames = links.map((link) => link.textContent?.trim() || link.getAttribute("aria-label") || "");

    expect(linkNames.indexOf("Back to Assistant")).toBeGreaterThanOrEqual(0);
    expect(linkNames.indexOf("Back to Assistant")).toBeLessThan(linkNames.indexOf("Users"));
    expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute("aria-current", "page");
    expect(container.querySelector('span.font-serif')).toHaveTextContent("NOA");
  });
});
