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
  let originalLocation: Location;

  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    originalLocation = window.location;
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      writable: true,
      value: originalLocation,
    });
  });

  it("returns null and calls clearAuth('session_expired') when sessionStorage token is expired", async () => {
    const locationSpy = { ...originalLocation, href: "http://localhost/assistant" };
    Object.defineProperty(window, "location", { writable: true, value: locationSpy });

    window.sessionStorage.setItem(TOKEN_KEY, EXPIRED_TOKEN);

    const { getAuthToken, _resetForTesting } = await import("./auth-store");
    _resetForTesting();
    const result = getAuthToken();

    expect(result).toBeNull();
    expect(window.sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(locationSpy.href).toContain("/login");
    expect(locationSpy.href).toContain("reason=session_expired");
  });

  // Legacy localStorage token migration was removed — getAuthToken only reads sessionStorage.

  it("returns a valid token from sessionStorage without clearing", async () => {
    window.sessionStorage.setItem(TOKEN_KEY, FUTURE_TOKEN);

    const { getAuthToken } = await import("./auth-store");
    const result = getAuthToken();

    expect(result).toBe(FUTURE_TOKEN);
  });

  it("does not read tokens from localStorage", async () => {
    window.localStorage.setItem(TOKEN_KEY, FUTURE_TOKEN);
    window.sessionStorage.clear();

    const { getAuthToken } = await import("./auth-store");
    expect(getAuthToken()).toBeNull();
  });
});

describe("cross-tab logout sync", () => {
  const TOKEN_KEY = "noa.jwt";
  const USER_KEY = "noa.user";

  let originalLocation: Location;

  beforeEach(() => {
    vi.resetModules();
    window.sessionStorage.clear();
    window.localStorage.clear();

    originalLocation = window.location;
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      writable: true,
      value: originalLocation,
    });
    // Clean up any BroadcastChannel stub
    if ("BroadcastChannel" in globalThis) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (globalThis as any).BroadcastChannel;
    }
  });

  it("clearAuth broadcasts a logout message", async () => {
    const locationSpy = { ...originalLocation, href: "http://localhost/assistant" };
    Object.defineProperty(window, "location", { writable: true, value: locationSpy });

    const postMessageSpy = vi.fn();
    const closeSpy = vi.fn();

    // Stub BroadcastChannel BEFORE importing the module
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).BroadcastChannel = vi.fn().mockImplementation(() => ({
      postMessage: postMessageSpy,
      close: closeSpy,
      onmessage: null,
    }));

    const mod = await import("./auth-store");
    mod._resetForTesting();
    mod.clearAuth("logged_out");

    expect(postMessageSpy).toHaveBeenCalledWith({ type: "noa:logout", reason: "logged_out" });
    expect(closeSpy).toHaveBeenCalled();
  });

  it("broadcasts session_expired reason when clearAuth is called with that reason", async () => {
    const locationSpy = { ...originalLocation, href: "http://localhost/assistant" };
    Object.defineProperty(window, "location", { writable: true, value: locationSpy });

    const postMessageSpy = vi.fn();
    const closeSpy = vi.fn();

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).BroadcastChannel = vi.fn().mockImplementation(() => ({
      postMessage: postMessageSpy,
      close: closeSpy,
      onmessage: null,
    }));

    const mod = await import("./auth-store");
    mod._resetForTesting();
    mod.clearAuth("session_expired");

    expect(postMessageSpy).toHaveBeenCalledWith({ type: "noa:logout", reason: "session_expired" });
  });

  it("receiving a logout broadcast clears storage", async () => {
    // Seed storage so we can verify it gets cleared
    window.sessionStorage.setItem(TOKEN_KEY, "some-token");
    window.localStorage.setItem(USER_KEY, JSON.stringify({ id: "1", email: "a@b.com" }));

    // Capture the onmessage handler set by listenForLogoutBroadcast()
    let capturedOnMessage: ((event: MessageEvent) => void) | null = null;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).BroadcastChannel = vi.fn().mockImplementation(() => {
      const instance = {
        postMessage: vi.fn(),
        close: vi.fn(),
        _onmessage: null as ((event: MessageEvent) => void) | null,
        get onmessage() {
          return this._onmessage;
        },
        set onmessage(handler: ((event: MessageEvent) => void) | null) {
          this._onmessage = handler;
          if (handler) capturedOnMessage = handler;
        },
      };
      return instance;
    });

    // Import and explicitly initialize the listener (module-level init may have
    // already run before BroadcastChannel was stubbed).
    const mod = await import("./auth-store");
    mod._initLogoutListenerForTesting();

    expect(capturedOnMessage).not.toBeNull();

    // Simulate receiving a logout message from another tab
    capturedOnMessage!({ data: { type: "noa:logout", reason: "session_expired" } } as MessageEvent);

    // Verify storage was cleared (redirect is tested via clearAuth broadcast tests;
    // jsdom's window.location cannot be reliably stubbed for handler-level redirects).
    expect(window.sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(window.localStorage.getItem(USER_KEY)).toBeNull();
  });
});
