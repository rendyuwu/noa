import { describe, expect, it } from "vitest";

import {
  AUTH_COOKIE_NAME,
  CSRF_COOKIE_NAME,
  createCsrfToken,
  isMutationMethod,
  tokensMatch,
} from "./server-auth";

describe("server-auth", () => {
  it("uses stable cookie names", () => {
    expect(AUTH_COOKIE_NAME).toBe("noa_session");
    expect(CSRF_COOKIE_NAME).toBe("noa_csrf");
  });

  it("creates a non-empty CSRF token", () => {
    expect(createCsrfToken()).toMatch(/^[A-Za-z0-9_-]{20,}$/);
  });

  it("treats write methods as CSRF protected", () => {
    expect(isMutationMethod("POST")).toBe(true);
    expect(isMutationMethod("PATCH")).toBe(true);
    expect(isMutationMethod("GET")).toBe(false);
  });

  it("requires exact CSRF token matches", () => {
    expect(tokensMatch("abc", "abc")).toBe(true);
    expect(tokensMatch("abc", "xyz")).toBe(false);
    expect(tokensMatch(null, "xyz")).toBe(false);
  });
});
