import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  cookies: {
    get: vi.fn(),
  },
  redirect: vi.fn((url: string) => {
    throw new Error(`REDIRECT ${url}`);
  }),
}));

vi.mock("next/headers", () => ({
  cookies: async () => mocks.cookies,
}));

vi.mock("next/navigation", () => ({
  redirect: (...args: [string]) => mocks.redirect(...args),
}));

import { fetchServerAuthUser, requireServerAdmin } from "./server-session";

describe("server-session", () => {
  it("returns null when the auth cookie is missing", async () => {
    mocks.cookies.get.mockReturnValue(undefined);
    await expect(fetchServerAuthUser()).resolves.toBeNull();
  });

  it("rejects non-admin users from admin routes", async () => {
    await expect(
      requireServerAdmin("/admin/users", {
        id: "1",
        email: "user@example.com",
        roles: ["member"],
      }),
    ).rejects.toThrow();
  });
});
