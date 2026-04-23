import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const USER_KEY = "noa.user";

describe("clearAuth", () => {
  let originalLocation: Location;

  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
    originalLocation = window.location;

    // Stub fetch for logout POST
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      writable: true,
      value: originalLocation,
    });
    vi.restoreAllMocks();
  });

  it("clears localStorage user and redirects to /login", async () => {
    const locationSpy = { ...originalLocation, href: "http://localhost/assistant" };
    Object.defineProperty(window, "location", { writable: true, value: locationSpy });

    window.localStorage.setItem(USER_KEY, JSON.stringify({ id: "1", email: "a@b.com" }));

    const mod = await import("./auth-store");
    mod._resetForTesting();
    mod.clearAuth("logged_out");

    expect(window.localStorage.getItem(USER_KEY)).toBeNull();
    expect(locationSpy.href).toContain("/login");
  });

  it("fires a POST to /api/auth/logout", async () => {
    const locationSpy = { ...originalLocation, href: "http://localhost/assistant" };
    Object.defineProperty(window, "location", { writable: true, value: locationSpy });

    const mod = await import("./auth-store");
    mod._resetForTesting();
    mod.clearAuth("session_expired");

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/auth/logout", {
      method: "POST",
      credentials: "include",
    });
  });

  it("is idempotent — second call is a no-op", async () => {
    const locationSpy = { ...originalLocation, href: "http://localhost/assistant" };
    Object.defineProperty(window, "location", { writable: true, value: locationSpy });

    const mod = await import("./auth-store");
    mod._resetForTesting();
    mod.clearAuth("logged_out");
    mod.clearAuth("logged_out");

    // fetch called only once
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });
});

describe("useRequireAuth guard", () => {
  it("checks localStorage user (not sessionStorage token)", async () => {
    // With no user in localStorage, the guard should redirect
    window.localStorage.clear();

    vi.resetModules();
    const mod = await import("./auth-store");
    const user = mod.getAuthUser();
    expect(user).toBeNull();

    // With a user in localStorage, the guard should allow
    window.localStorage.setItem(USER_KEY, JSON.stringify({ id: "1", email: "a@b.com" }));
    const user2 = mod.getAuthUser();
    expect(user2).not.toBeNull();
    expect(user2!.email).toBe("a@b.com");
  });
});

describe("cross-tab logout sync", () => {
  let originalLocation: Location;

  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
    originalLocation = window.location;
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      writable: true,
      value: originalLocation,
    });
    if ("BroadcastChannel" in globalThis) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (globalThis as any).BroadcastChannel;
    }
  });

  it("clearAuth broadcasts a logout message", async () => {
    const locationSpy = { ...originalLocation, href: "http://localhost/assistant" };
    Object.defineProperty(window, "location", { writable: true, value: locationSpy });

    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));

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
    mod.clearAuth("logged_out");

    expect(postMessageSpy).toHaveBeenCalledWith({ type: "noa:logout", reason: "logged_out" });
    expect(closeSpy).toHaveBeenCalled();
  });

  it("receiving a logout broadcast clears localStorage", async () => {
    window.localStorage.setItem(USER_KEY, JSON.stringify({ id: "1", email: "a@b.com" }));

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
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

    const mod = await import("./auth-store");
    mod._initLogoutListenerForTesting();

    expect(capturedOnMessage).not.toBeNull();

    capturedOnMessage!({ data: { type: "noa:logout", reason: "session_expired" } } as MessageEvent);

    expect(window.localStorage.getItem(USER_KEY)).toBeNull();
  });
});
