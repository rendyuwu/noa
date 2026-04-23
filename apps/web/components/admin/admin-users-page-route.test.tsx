import type { PropsWithChildren } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  authReady: true,
  isAdmin: true,
}));

vi.mock("@/components/lib/use-verified-auth", () => ({
  useVerifiedAuth: () => ({
    ready: mocks.authReady && mocks.isAdmin,
    user: mocks.isAdmin
      ? { id: "1", email: "admin@example.com", display_name: null, is_active: true, roles: ["admin"] }
      : { id: "1", email: "user@example.com", display_name: null, is_active: true, roles: ["member"] },
    isAdmin: mocks.isAdmin,
  }),
}));

vi.mock("@/components/lib/runtime-provider", () => ({
  NoaAssistantRuntimeProvider: ({ children }: PropsWithChildren) => (
    <div data-testid="runtime-provider">{children}</div>
  ),
}));

vi.mock("@/components/admin/admin-shell", () => ({
  AdminShell: ({ children }: PropsWithChildren) => (
    <div data-testid="admin-shell">{children}</div>
  ),
}));

vi.mock("@/components/admin/users-admin-page", () => ({
  UsersAdminPage: () => <div data-testid="users-admin-page" />,
}));

import AdminUsersPage from "@/app/(admin)/admin/users/page";

describe("/admin/users route wrappers", () => {
  beforeEach(() => {
    mocks.authReady = true;
    mocks.isAdmin = true;
  });

  it("renders users page inside the admin shell when auth is ready and user is admin", () => {
    render(<AdminUsersPage />);

    const adminShell = screen.getByTestId("admin-shell");
    const usersPage = screen.getByTestId("users-admin-page");

    expect(adminShell).toContainElement(usersPage);
  });

  it("renders null when auth is not ready", () => {
    mocks.authReady = false;

    const { container } = render(<AdminUsersPage />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders null when user is not an admin", () => {
    mocks.isAdmin = false;

    const { container } = render(<AdminUsersPage />);

    expect(container).toBeEmptyDOMElement();
  });
});
