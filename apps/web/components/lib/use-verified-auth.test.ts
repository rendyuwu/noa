import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
  getAuthToken: vi.fn(),
  clearAuth: vi.fn(),
  setAuthUser: vi.fn(),
  isAuthRedirectError: vi.fn().mockReturnValue(false),
  replace: vi.fn(),
}));

vi.mock("@/components/lib/fetch-helper", () => ({
  fetchWithAuth: (...args: unknown[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: unknown[]) => mocks.jsonOrThrow(...args),
}));

vi.mock("@/components/lib/auth-store", () => ({
  getAuthToken: () => mocks.getAuthToken(),
  clearAuth: (...args: unknown[]) => mocks.clearAuth(...args),
  setAuthUser: (...args: unknown[]) => mocks.setAuthUser(...args),
  isAuthRedirectError: (e: unknown) => mocks.isAuthRedirectError(e),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

import { useVerifiedAuth } from "./use-verified-auth";

describe("useVerifiedAuth", () => {
  beforeEach(() => {
    for (const m of Object.values(mocks)) m.mockReset?.();
    mocks.isAuthRedirectError.mockReturnValue(false);
  });

  it("returns verified user with roles after /auth/me succeeds", async () => {
    mocks.getAuthToken.mockReturnValue("valid-token");
    const meResponse = { user: { id: "u1", email: "admin@test.com", display_name: "Admin", roles: ["admin"], is_active: true } };
    mocks.fetchWithAuth.mockResolvedValue(new Response());
    mocks.jsonOrThrow.mockResolvedValue(meResponse);

    const { result } = renderHook(() => useVerifiedAuth());

    await waitFor(() => {
      expect(result.current.ready).toBe(true);
    });

    expect(result.current.user).toEqual(meResponse.user);
    expect(result.current.isAdmin).toBe(true);
    expect(mocks.setAuthUser).toHaveBeenCalledWith({
      id: "u1",
      email: "admin@test.com",
      display_name: "Admin",
      roles: ["admin"],
    });
  });

  it("does not become ready when fetchWithAuth rejects", async () => {
    mocks.fetchWithAuth.mockRejectedValue(new Error("network failure"));

    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { result } = renderHook(() => useVerifiedAuth());

    // Give the async effect time to settle
    await new Promise((r) => setTimeout(r, 50));

    expect(result.current.ready).toBe(false);
    expect(result.current.user).toBeNull();
    consoleSpy.mockRestore();
  });

  it("redirects to /assistant when user is not admin and requireAdmin is true", async () => {
    mocks.getAuthToken.mockReturnValue("valid-token");
    const meResponse = { user: { id: "u2", email: "user@test.com", roles: [], is_active: true } };
    mocks.fetchWithAuth.mockResolvedValue(new Response());
    mocks.jsonOrThrow.mockResolvedValue(meResponse);

    renderHook(() => useVerifiedAuth({ requireAdmin: true }));

    await waitFor(() => {
      expect(mocks.replace).toHaveBeenCalledWith("/assistant");
    });
  });

  it("does not redirect non-admin users when requireAdmin is false", async () => {
    mocks.getAuthToken.mockReturnValue("valid-token");
    const meResponse = { user: { id: "u2", email: "user@test.com", roles: ["member"], is_active: true } };
    mocks.fetchWithAuth.mockResolvedValue(new Response());
    mocks.jsonOrThrow.mockResolvedValue(meResponse);

    const { result } = renderHook(() => useVerifiedAuth());

    await waitFor(() => {
      expect(result.current.ready).toBe(true);
    });

    expect(result.current.isAdmin).toBe(false);
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("calls /auth/me via fetchWithAuth", async () => {
    mocks.getAuthToken.mockReturnValue("valid-token");
    const meResponse = { user: { id: "u1", email: "a@test.com", roles: [], is_active: true } };
    mocks.fetchWithAuth.mockResolvedValue(new Response());
    mocks.jsonOrThrow.mockResolvedValue(meResponse);

    renderHook(() => useVerifiedAuth());

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        "/auth/me",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  it("suppresses AuthRedirectError without logging", async () => {
    mocks.getAuthToken.mockReturnValue("valid-token");
    const authError = new Error("Auth redirect in progress");
    mocks.fetchWithAuth.mockRejectedValue(authError);
    mocks.isAuthRedirectError.mockReturnValue(true);

    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { result } = renderHook(() => useVerifiedAuth());

    // Give the async effect time to settle
    await new Promise((r) => setTimeout(r, 50));

    expect(result.current.ready).toBe(false);
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it("does not update state after unmount", async () => {
    mocks.getAuthToken.mockReturnValue("valid-token");

    let resolveMe: (value: unknown) => void;
    const mePromise = new Promise((resolve) => { resolveMe = resolve; });
    mocks.fetchWithAuth.mockReturnValue(mePromise);

    const { result, unmount } = renderHook(() => useVerifiedAuth());

    // Unmount before the fetch resolves
    unmount();

    // Now resolve the fetch
    const meResponse = { user: { id: "u1", email: "a@test.com", roles: ["admin"], is_active: true } };
    mocks.jsonOrThrow.mockResolvedValue(meResponse);
    resolveMe!(new Response());

    await new Promise((r) => setTimeout(r, 50));

    // State should NOT have been updated
    expect(result.current.ready).toBe(false);
  });
});
