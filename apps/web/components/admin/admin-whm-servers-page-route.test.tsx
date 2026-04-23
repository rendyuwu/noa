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

vi.mock("@/components/admin/whm-servers-admin-page", () => ({
  WhmServersAdminPage: () => <div data-testid="whm-servers-admin-page" />,
}));

import AdminWhmServersPage from "@/app/(admin)/admin/whm/servers/page";

describe("/admin/whm/servers route wrappers", () => {
  beforeEach(() => {
    mocks.authReady = true;
    mocks.isAdmin = true;
  });

  it("renders WHM servers page inside the admin shell when auth is ready and user is admin", () => {
    render(<AdminWhmServersPage />);

    const adminShell = screen.getByTestId("admin-shell");
    const whmServersPage = screen.getByTestId("whm-servers-admin-page");

    expect(adminShell).toContainElement(whmServersPage);
  });

  it("renders null when auth is not ready", () => {
    mocks.authReady = false;

    const { container } = render(<AdminWhmServersPage />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders null when user is not an admin", () => {
    mocks.isAdmin = false;

    const { container } = render(<AdminWhmServersPage />);
    expect(container).toBeEmptyDOMElement();
  });
});
