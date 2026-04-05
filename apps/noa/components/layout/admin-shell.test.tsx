import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  pathname: "/admin/users",
  clearAuth: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
}));

vi.mock("@/components/lib/auth/auth-storage", () => ({
  clearAuth: (...args: unknown[]) => mocks.clearAuth(...args),
}));

vi.mock("@/components/ui/theme-toggle", () => ({
  ThemeToggle: () => <div data-testid="theme-toggle" />,
}));

import { AdminShell } from "./admin-shell";

describe("AdminShell", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mocks.pathname = "/admin/users";
    mocks.clearAuth.mockReset();
  });

  it("renders the admin navigation and shell copy", () => {
    const { container } = render(
      <AdminShell
        title="Users"
        description="Manage user activation, roles, and permissions."
        user={{ id: "user-1", email: "admin@example.com", display_name: "Admin User" }}
      >
        <div>Admin content</div>
      </AdminShell>,
    );

    expect(container.firstChild).toHaveClass("h-dvh", "overflow-hidden");
    expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(
      screen.getByText("Manage user activation, roles, and permissions."),
    ).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Back to chat" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Roles" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Audit" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "WHM" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Proxmox" })).toBeInTheDocument();

    expect(screen.getByText("Signed in as")).toBeInTheDocument();
    expect(screen.getByText("Admin User")).toBeInTheDocument();
  });

  it("keeps the mobile drawer expanded with labels and identity copy", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <AdminShell
        title="Users"
        description="Manage user activation, roles, and permissions."
        user={{ id: "user-1", email: "admin@example.com", display_name: "Admin User" }}
      >
        <div>Admin content</div>
      </AdminShell>,
    );

    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    await user.click(screen.getByRole("button", { name: "Open navigation" }));

    expect(container.firstChild).toHaveClass("h-dvh", "overflow-hidden");
    expect(screen.getByRole("dialog", { name: "Admin navigation" })).toBeInTheDocument();

    const closeButtons = screen.getAllByRole("button", { name: "Close navigation" });
    const mobileSidebar = closeButtons.at(-1)?.closest("aside");

    expect(mobileSidebar).not.toBeNull();
    expect(within(mobileSidebar as HTMLElement).getByRole("link", { name: "Users" })).toBeInTheDocument();
    expect(within(mobileSidebar as HTMLElement).getByText("Signed in as")).toBeInTheDocument();
    expect(within(mobileSidebar as HTMLElement).getByText("Admin User")).toBeInTheDocument();
  });

  it("signs out through the shared auth helper", async () => {
    render(
      <AdminShell
        title="Users"
        description="Manage user activation, roles, and permissions."
        user={{ id: "user-1", email: "admin@example.com", display_name: "Admin User" }}
      >
        <div>Admin content</div>
      </AdminShell>,
    );

    screen.getByRole("button", { name: "Sign out" }).click();

    expect(mocks.clearAuth).toHaveBeenCalledWith({ returnTo: "/assistant", redirect: true });
  });

  it("keeps collapsed navigation actions accessible by name", async () => {
    const user = userEvent.setup();

    render(
      <AdminShell
        title="Users"
        description="Manage user activation, roles, and permissions."
        user={{ id: "user-1", email: "admin@example.com", display_name: "Admin User" }}
      >
        <div>Admin content</div>
      </AdminShell>,
    );

    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));

    expect(screen.getByRole("link", { name: "Back to chat" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Roles" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Audit" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "WHM" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Proxmox" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});
