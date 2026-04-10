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

vi.mock("@/components/admin/admin-shell", () => ({
  AdminShell: ({ children }: PropsWithChildren) => (
    <div data-testid="admin-shell">{children}</div>
  ),
}));

vi.mock("@/components/admin/proxmox-servers-admin-page", () => ({
  ProxmoxServersAdminPage: () => <div data-testid="proxmox-servers-admin-page" />,
}));

import AdminProxmoxServersPage from "@/app/(admin)/admin/proxmox/servers/page";

describe("/admin/proxmox/servers route wrappers", () => {
  beforeEach(() => {
    mocks.authReady = true;
    mocks.isAdmin = true;
  });

  it("renders Proxmox servers page inside the admin shell when auth is ready and user is admin", () => {
    render(<AdminProxmoxServersPage />);

    const adminShell = screen.getByTestId("admin-shell");
    const proxmoxServersPage = screen.getByTestId("proxmox-servers-admin-page");

    expect(adminShell).toContainElement(proxmoxServersPage);
  });

  it("renders null when auth is not ready", () => {
    mocks.authReady = false;

    const { container } = render(<AdminProxmoxServersPage />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders null when user is not an admin", () => {
    mocks.isAdmin = false;

    const { container } = render(<AdminProxmoxServersPage />);
    expect(container).toBeEmptyDOMElement();
  });
});
