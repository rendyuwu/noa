import { describe, expect, it, vi } from "vitest";

const useAuthSessionMock = vi.fn();

vi.mock("./auth-session", () => ({
  useAuthSession: () => useAuthSessionMock(),
}));

import { useRequireAuth } from "./use-require-auth";

describe("useRequireAuth", () => {
  it("returns the auth session contract from useAuthSession", () => {
    const session = {
      error: null,
      ready: true,
      refresh: vi.fn(async () => null),
      user: { id: "1", email: "admin@example.com", roles: ["admin"] },
      validating: false,
    };
    useAuthSessionMock.mockReturnValue(session);

    expect(useRequireAuth()).toBe(session);
  });
});
