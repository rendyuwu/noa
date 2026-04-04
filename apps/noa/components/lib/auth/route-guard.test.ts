import { describe, expect, it } from "vitest";

import { buildLoginRedirect, isProtectedPath } from "./route-guard";

describe("route-guard", () => {
  it("treats /assistant and /admin paths as protected", () => {
    expect(isProtectedPath("/assistant")).toBe(true);
    expect(isProtectedPath("/assistant/thread-1")).toBe(true);
    expect(isProtectedPath("/admin/users")).toBe(true);
    expect(isProtectedPath("/login")).toBe(false);
  });

  it("builds a login redirect that preserves the returnTo path", () => {
    expect(buildLoginRedirect("https://app.example.com/admin/users?tab=roles")).toBe(
      "/login?returnTo=%2Fadmin%2Fusers%3Ftab%3Droles",
    );
  });
});
