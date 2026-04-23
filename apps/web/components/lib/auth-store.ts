"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const USER_KEY = "noa.user";

export type AuthUser = {
  id: string;
  email: string;
  display_name?: string | null;
  roles?: string[];
};

export type ClearAuthReason = "session_expired" | "logged_out";

/**
 * Sentinel error thrown by `fetchWithAuth` (and helpers) when a 401 triggers
 * an auth redirect.  Callers that catch generic errors can check for this to
 * avoid noisy logging / spurious error UI while the redirect is in flight.
 */
export class AuthRedirectError extends Error {
  constructor(reason: ClearAuthReason = "session_expired") {
    super(`Auth redirect in progress (${reason})`);
    this.name = "AuthRedirectError";
  }
}

/** Returns `true` when the given value is an `AuthRedirectError`. */
export const isAuthRedirectError = (error: unknown): error is AuthRedirectError => {
  return error instanceof AuthRedirectError;
};

export const setAuthUser = (user: AuthUser | null): void => {
  if (user === null) {
    window.localStorage.removeItem(USER_KEY);
    return;
  }
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
};

export const getAuthUser = (): AuthUser | null => {
  if (typeof window === "undefined") {
    return null;
  }

  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
};

// ---------------------------------------------------------------------------
// clearAuth – idempotent logout with cross-tab broadcast
// ---------------------------------------------------------------------------

let _clearAuthInProgress = false;

export const isClearAuthInProgress = (): boolean => _clearAuthInProgress;

/** Reset the idempotency guard — **test-only**. */
export const _resetForTesting = (): void => {
  _clearAuthInProgress = false;
};

/** Manually wire up the cross-tab listener — **test-only**. */
export const _initLogoutListenerForTesting = (): void => {
  initLogoutListener();
};

function getSafeReturnTo(): string {
  if (typeof window === "undefined") return "/assistant";
  const raw = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/login")) {
    return "/assistant";
  }
  return raw;
}

function broadcastLogout(reason?: ClearAuthReason): void {
  try {
    if (typeof BroadcastChannel === "undefined") return;
    const ch = new BroadcastChannel("noa:auth");
    ch.postMessage({ type: "noa:logout", reason });
    ch.close();
  } catch {
    // BroadcastChannel may not be available in all environments.
  }
}

export const clearAuth = (reason?: ClearAuthReason): void => {
  if (typeof window === "undefined") return;

  // Idempotent: only one redirect per page lifecycle.
  if (_clearAuthInProgress) return;
  _clearAuthInProgress = true;

  // Clear server-side cookie (fire-and-forget).
  fetch("/api/auth/logout", { method: "POST", credentials: "include" }).catch(() => {
    // Best-effort — cookie expires naturally if this fails.
  });

  window.localStorage.removeItem(USER_KEY);

  // Notify other tabs so they redirect to login as well.
  broadcastLogout(reason);

  const params = new URLSearchParams();
  if (reason) {
    params.set("reason", reason);
  }
  const returnTo = getSafeReturnTo();
  if (returnTo !== "/assistant") {
    params.set("returnTo", returnTo);
  }
  const qs = params.toString();
  window.location.href = qs ? `/login?${qs}` : "/login";
};

export const useRequireAuth = (): boolean => {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const user = getAuthUser();
    if (!user) {
      const returnTo = getSafeReturnTo();
      const params = new URLSearchParams();
      if (returnTo !== "/assistant") {
        params.set("returnTo", returnTo);
      }
      const qs = params.toString();
      router.replace(qs ? `/login?${qs}` : "/login");
      return;
    }
    setReady(true);
  }, [router]);

  return ready;
};

// ---------------------------------------------------------------------------
// Cross-tab logout listener
// ---------------------------------------------------------------------------

function initLogoutListener(): void {
  try {
    if (typeof BroadcastChannel === "undefined") return;
    const ch = new BroadcastChannel("noa:auth");
    ch.onmessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; reason?: ClearAuthReason } | undefined;
      if (data?.type !== "noa:logout") return;

      window.localStorage.removeItem(USER_KEY);

      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
    };
  } catch {
    // BroadcastChannel may not be available.
  }
}

// Auto-init the listener when the module loads in a browser context.
if (typeof window !== "undefined") {
  initLogoutListener();
}
