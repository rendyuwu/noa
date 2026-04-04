import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  clearAuth: vi.fn(),
  getAuthUser: vi.fn(),
  jsonOrThrow: vi.fn(),
  fetchWithAuth: vi.fn(),
  replace: vi.fn(),
  reportClientError: vi.fn(),
  setAuthUser: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/users",
  useRouter: () => ({
    replace: (...args: unknown[]) => mocks.replace(...args),
  }),
}));

vi.mock("./auth-storage", () => ({
  clearAuth: (...args: unknown[]) => mocks.clearAuth(...args),
  getAuthUser: () => mocks.getAuthUser(),
  setAuthUser: (...args: unknown[]) => mocks.setAuthUser(...args),
}));

vi.mock("@/components/lib/http/fetch-client", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    errorCode?: string;
    requestId?: string;

    constructor(status: number, detail: string, options: { errorCode?: string; requestId?: string } = {}) {
      super(detail);
      this.status = status;
      this.errorCode = options.errorCode;
      this.requestId = options.requestId;
    }
  },
  fetchWithAuth: (...args: unknown[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: unknown[]) => mocks.jsonOrThrow(...args),
}));

vi.mock("@/components/lib/observability/error-reporting", () => ({
  reportClientError: (...args: unknown[]) => mocks.reportClientError(...args),
}));

import { useAuthSession } from "./auth-session";

function SessionProbe() {
  const session = useAuthSession();

  return (
    <div>
      <div data-testid="ready">{String(session.ready)}</div>
      <div data-testid="validating">{String(session.validating)}</div>
      <div data-testid="user">{session.user?.email ?? "none"}</div>
      <div data-testid="error">{session.error ?? "none"}</div>
      <button type="button" onClick={() => void session.refresh()}>
        refresh
      </button>
    </div>
  );
}

describe("useAuthSession", () => {
  beforeEach(() => {
    mocks.clearAuth.mockReset();
    mocks.getAuthUser.mockReset();
    mocks.jsonOrThrow.mockReset();
    mocks.fetchWithAuth.mockReset();
    mocks.replace.mockReset();
    mocks.reportClientError.mockReset();
    mocks.setAuthUser.mockReset();
    window.history.replaceState({}, "", "/admin/users");
  });

  it("validates the session even when no browser token exists", async () => {
    mocks.getAuthUser.mockReturnValue({
      id: "1",
      email: "cached@example.com",
      roles: ["admin"],
    });
    mocks.fetchWithAuth.mockResolvedValue(new Response(null, { status: 200 }));
    mocks.jsonOrThrow.mockResolvedValue({
      user: {
        id: "1",
        email: "fresh@example.com",
        roles: ["admin"],
      },
    });

    render(<SessionProbe />);

    await waitFor(() => {
      expect(screen.getByTestId("ready")).toHaveTextContent("true");
    });

    expect(screen.getByTestId("user")).toHaveTextContent("fresh@example.com");
    expect(mocks.setAuthUser).toHaveBeenCalledWith({
      id: "1",
      email: "fresh@example.com",
      roles: ["admin"],
    });
  });

  it("clears auth when validation returns an authorization failure", async () => {
    const { ApiError } = await import("@/components/lib/http/fetch-client");
    mocks.getAuthUser.mockReturnValue({
      id: "1",
      email: "admin@example.com",
      roles: ["admin"],
    });
    mocks.fetchWithAuth.mockResolvedValue(new Response(null, { status: 403 }));
    mocks.jsonOrThrow.mockRejectedValue(new ApiError(403, "Forbidden", { errorCode: "admin_access_required" }));

    render(<SessionProbe />);

    await waitFor(() => {
      expect(mocks.clearAuth).toHaveBeenCalled();
    });

    expect(mocks.reportClientError).not.toHaveBeenCalled();
  });

  it("surfaces transient validation failures with a retry-safe error state", async () => {
    mocks.getAuthUser.mockReturnValue({
      id: "1",
      email: "admin@example.com",
      roles: ["admin"],
    });
    mocks.fetchWithAuth.mockRejectedValueOnce(new Error("socket hang up"));
    mocks.fetchWithAuth.mockResolvedValue(new Response(null, { status: 200 }));
    mocks.jsonOrThrow.mockResolvedValue({
      user: {
        id: "1",
        email: "admin@example.com",
        roles: ["admin"],
      },
    });

    render(<SessionProbe />);

    await waitFor(() => {
      expect(screen.getByTestId("error")).toHaveTextContent("We couldn't verify your session. Retry to continue.");
    });

    expect(mocks.reportClientError).toHaveBeenCalledWith(expect.any(Error), {
      source: "auth.session.refresh",
    });

    fireEvent.click(screen.getByRole("button", { name: "refresh" }));

    await waitFor(() => {
      expect(screen.getByTestId("ready")).toHaveTextContent("true");
    });
  });
});
