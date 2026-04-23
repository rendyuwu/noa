import { beforeEach, describe, expect, it, vi } from "vitest";

// Pre-computed JWT tokens for testing
const EXPIRED_TOKEN =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0QGV4YW1wbGUuY29tIiwiZXhwIjoxMDAwfQ.signature";
const FUTURE_TOKEN =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0QGV4YW1wbGUuY29tIiwiZXhwIjo5OTk5OTk5OTk5fQ.signature";
const NO_EXP_TOKEN =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0QGV4YW1wbGUuY29tIn0.signature";

describe("isTokenExpired", () => {
  let isTokenExpired: (token: string) => boolean;

  beforeEach(async () => {
    vi.resetModules();
    const mod = await import("./auth-store");
    isTokenExpired = mod.isTokenExpired;
  });

  it("returns true for an expired token (exp in the past)", () => {
    expect(isTokenExpired(EXPIRED_TOKEN)).toBe(true);
  });

  it("returns false for a valid token (exp in the future)", () => {
    expect(isTokenExpired(FUTURE_TOKEN)).toBe(false);
  });

  it("returns false (fail-open) for a malformed token", () => {
    expect(isTokenExpired("not-a-jwt")).toBe(false);
    expect(isTokenExpired("")).toBe(false);
    expect(isTokenExpired("a.b")).toBe(false);
    expect(isTokenExpired("a.!!!.c")).toBe(false);
  });

  it("returns false when the exp claim is missing", () => {
    expect(isTokenExpired(NO_EXP_TOKEN)).toBe(false);
  });

  it("returns false when exp is not a number", () => {
    // Build a token with exp as a string
    const payload = btoa(JSON.stringify({ sub: "test", exp: "not-a-number" }));
    const token = `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
    expect(isTokenExpired(token)).toBe(false);
  });

  it("handles base64url characters (- and _) and missing padding", () => {
    // Payload with characters that produce base64url-specific encoding.
    // {"sub":"test+/test","exp":1000} — the +/ in the value forces base64url
    // chars in the encoded output.
    const payloadJson = JSON.stringify({ sub: "test+/test", exp: 1000 });
    const base64url = btoa(payloadJson)
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    const token = `eyJhbGciOiJIUzI1NiJ9.${base64url}.sig`;
    expect(isTokenExpired(token)).toBe(true);
  });

  it("treats token as expired only when exp is more than 30s in the past", () => {
    const nowSec = Math.floor(Date.now() / 1000);

    // Token that expired 10 seconds ago — within 30s grace, should NOT be treated as expired
    const recentPayload = btoa(JSON.stringify({ sub: "test", exp: nowSec - 10 }));
    const recentToken = `eyJhbGciOiJIUzI1NiJ9.${recentPayload}.sig`;
    expect(isTokenExpired(recentToken)).toBe(false);

    // Token that expired 60 seconds ago — outside 30s grace, SHOULD be treated as expired
    const oldPayload = btoa(JSON.stringify({ sub: "test", exp: nowSec - 60 }));
    const oldToken = `eyJhbGciOiJIUzI1NiJ9.${oldPayload}.sig`;
    expect(isTokenExpired(oldToken)).toBe(true);
  });
});

describe("getAuthToken with expired tokens", () => {
  const TOKEN_KEY = "noa.jwt";

  beforeEach(() => {
    vi.resetModules();
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it("returns null and calls clearAuth('session_expired') when sessionStorage token is expired", async () => {
    // Stub window.location to prevent jsdom navigation error
    const originalLocation = window.location;
    const locationSpy = { ...originalLocation, href: "http://localhost/assistant" };
    Object.defineProperty(window, "location", {
      writable: true,
      value: locationSpy,
    });

    try {
      window.sessionStorage.setItem(TOKEN_KEY, EXPIRED_TOKEN);

      const { getAuthToken } = await import("./auth-store");
      const result = getAuthToken();

      expect(result).toBeNull();
      expect(window.sessionStorage.getItem(TOKEN_KEY)).toBeNull();
      // Verify clearAuth was called with the correct reason by checking the redirect URL
      expect(locationSpy.href).toContain("/login");
      expect(locationSpy.href).toContain("reason=session_expired");
    } finally {
      Object.defineProperty(window, "location", {
        writable: true,
        value: originalLocation,
      });
    }
  });

  it("returns null and calls clearAuth('session_expired') when legacy localStorage token is expired", async () => {
    const originalLocation = window.location;
    const locationSpy = { ...originalLocation, href: "http://localhost/assistant" };
    Object.defineProperty(window, "location", {
      writable: true,
      value: locationSpy,
    });

    try {
      window.localStorage.setItem(TOKEN_KEY, EXPIRED_TOKEN);

      const { getAuthToken } = await import("./auth-store");
      const result = getAuthToken();

      expect(result).toBeNull();
      expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
      expect(locationSpy.href).toContain("/login");
      expect(locationSpy.href).toContain("reason=session_expired");
    } finally {
      Object.defineProperty(window, "location", {
        writable: true,
        value: originalLocation,
      });
    }
  });

  it("returns a valid token from sessionStorage without clearing", async () => {
    window.sessionStorage.setItem(TOKEN_KEY, FUTURE_TOKEN);

    const { getAuthToken } = await import("./auth-store");
    const result = getAuthToken();

    expect(result).toBe(FUTURE_TOKEN);
  });

  it("migrates a valid legacy token from localStorage to sessionStorage", async () => {
    window.localStorage.setItem(TOKEN_KEY, FUTURE_TOKEN);

    const { getAuthToken } = await import("./auth-store");
    const result = getAuthToken();

    expect(result).toBe(FUTURE_TOKEN);
    expect(window.sessionStorage.getItem(TOKEN_KEY)).toBe(FUTURE_TOKEN);
    expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});
