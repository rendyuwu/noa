import type { PropsWithChildren } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  session: {
    error: null as string | null,
    ready: true,
    refresh: vi.fn(async () => null),
    user: {
      id: "1",
      email: "admin@example.com",
      roles: ["admin"],
    } as { id: string; email: string; roles: string[] } | null,
    validating: false,
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: (...args: unknown[]) => mocks.replace(...args),
  }),
}));

vi.mock("@/components/lib/auth/auth-session", () => ({
  useAuthSession: () => mocks.session,
}));

vi.mock("./app-shell", () => ({
  AppShell: ({ children, user, title }: PropsWithChildren<{ user: { email: string }; title: string }>) => (
    <div data-testid="app-shell" data-user={user.email} data-title={title}>
      {children}
    </div>
  ),
}));

import { ProtectedScreen } from "./protected-screen";

describe("ProtectedScreen", () => {
  beforeEach(() => {
    mocks.replace.mockReset();
    mocks.session.error = null;
    mocks.session.ready = true;
    mocks.session.refresh.mockReset();
    mocks.session.user = {
      id: "1",
      email: "admin@example.com",
      roles: ["admin"],
    };
    mocks.session.validating = false;
  });

  it("renders the shell with the validated user", () => {
    render(
      <ProtectedScreen title="Assistant" description="Manage your workspace">
        <div>child</div>
      </ProtectedScreen>,
    );

    expect(screen.getByTestId("app-shell")).toHaveAttribute("data-user", "admin@example.com");
    expect(screen.getByText("child")).toBeInTheDocument();
  });

  it("blocks on transient validation failures and offers retry", () => {
    mocks.session.error = "We couldn't verify your session. Retry to continue.";
    mocks.session.ready = false;
    mocks.session.user = null;

    render(
      <ProtectedScreen title="Users" description="Manage users" requireAdmin>
        <div>child</div>
      </ProtectedScreen>,
    );

    expect(screen.getByText("Session validation failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry validation" })).toBeInTheDocument();
  });

  it("redirects downgraded users away from admin pages after validation", () => {
    mocks.session.user = {
      id: "2",
      email: "member@example.com",
      roles: ["member"],
    };

    render(
      <ProtectedScreen title="Users" description="Manage users" requireAdmin>
        <div>child</div>
      </ProtectedScreen>,
    );

    expect(mocks.replace).toHaveBeenCalledWith("/assistant");
    expect(screen.getByText("Loading your workspace…")).toBeInTheDocument();
  });
});
