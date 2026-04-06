import type { PropsWithChildren } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  authReady: true,
  isAdmin: true,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: () => {},
  }),
}));

vi.mock("@/components/lib/auth-store", () => ({
  useRequireAuth: () => mocks.authReady,
  getAuthUser: () => ({
    id: "1",
    email: "admin@example.com",
    roles: mocks.isAdmin ? ["admin"] : ["member"],
  }),
}));

vi.mock("@/components/lib/runtime-provider", () => ({
  NoaAssistantRuntimeProvider: ({ children }: PropsWithChildren) => (
    <div data-testid="runtime-provider">{children}</div>
  ),
}));

vi.mock("@/components/admin/admin-sidebar-shell", () => ({
  AdminSidebarShell: ({ children }: PropsWithChildren) => (
    <div data-testid="admin-sidebar-shell">{children}</div>
  ),
}));

vi.mock("@/components/admin/roles-admin-page", () => ({
  RolesAdminPage: () => <div data-testid="roles-admin-page" />,
}));

import AdminRolesPage from "@/app/(admin)/admin/roles/page";

describe("/admin/roles route wrappers", () => {
  beforeEach(() => {
    mocks.authReady = true;
    mocks.isAdmin = true;
  });

  it("renders roles page inside runtime provider and admin sidebar shell when auth is ready and user is admin", () => {
    render(<AdminRolesPage />);

    const runtimeProvider = screen.getByTestId("runtime-provider");
    const adminSidebarShell = screen.getByTestId("admin-sidebar-shell");
    const rolesPage = screen.getByTestId("roles-admin-page");

    expect(runtimeProvider).toContainElement(adminSidebarShell);
    expect(adminSidebarShell).toContainElement(rolesPage);
  });

  it("renders null when auth is not ready", () => {
    mocks.authReady = false;

    const { container } = render(<AdminRolesPage />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders null when user is not an admin", () => {
    mocks.isAdmin = false;

    const { container } = render(<AdminRolesPage />);
    expect(container).toBeEmptyDOMElement();
  });
});
