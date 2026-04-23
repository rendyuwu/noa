"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const TOKEN_KEY = "noa.jwt";
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

/**
 * Lightweight client-side JWT expiry check.
 *
 * Decodes the payload (base64url → JSON) and checks whether the `exp` claim
 * is in the past.  A 30-second grace window avoids false positives from minor
 * clock skew.
 *
 * **Fail-open**: returns `false` on any parse error so that the server remains
 * the ultimate authority on token validity.
 */
export const isTokenExpired = (token: string): boolean => {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return false;
    // JWT payload is base64url-encoded (no padding).  Restore standard base64.
    let base64 = parts[1]!.replace(/-/g, "+").replace(/_/g, "/");
    const pad = base64.length % 4;
    if (pad === 2) base64 += "==";
    else if (pad === 3) base64 += "=";
    const payload = JSON.parse(atob(base64)) as { exp?: unknown };
    if (typeof payload.exp !== "number") return false;
    return payload.exp < Date.now() / 1000 - 30;
  } catch {
    return false;
  }
};

export const getAuthToken = (): string | null => {
  if (typeof window === "undefined") {
    return null;
  }

  const sessionToken = window.sessionStorage.getItem(TOKEN_KEY);
  if (sessionToken) {
    if (isTokenExpired(sessionToken)) {
      clearAuth("session_expired");
      return null;
    }
    return sessionToken;
  }

  const legacyToken = window.localStorage.getItem(TOKEN_KEY);
  if (!legacyToken) {
    return null;
  }

  if (isTokenExpired(legacyToken)) {
    clearAuth("session_expired");
    return null;
  }

  window.sessionStorage.setItem(TOKEN_KEY, legacyToken);
  window.localStorage.removeItem(TOKEN_KEY);
  return legacyToken;
};

export const setAuthToken = (token: string): void => {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(TOKEN_KEY, token);
  window.localStorage.removeItem(TOKEN_KEY);
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
// Idempotent auth-clearing with reason + returnTo
// ---------------------------------------------------------------------------

let _clearAuthInProgress = false;

/**
 * Returns `true` when a `clearAuth` redirect is already in flight.
 * Useful for callers that want to suppress secondary error handling.
 */
export const isClearAuthInProgress = (): boolean => _clearAuthInProgress;

/** @internal Reset module-scoped flags for testing only. */
export const _resetForTesting = (): void => {
  _clearAuthInProgress = false;
  _logoutListenerInitialized = false;
};

/** @internal Re-initialize the logout broadcast listener for testing. */
export const _initLogoutListenerForTesting = (): void => {
  _logoutListenerInitialized = false;
  listenForLogoutBroadcast();
};

function getSafeReturnTo(): string {
  if (typeof window === "undefined") return "/assistant";
  const raw = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  // Only allow relative paths that don't escape to a different origin.
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/assistant";
  // Don't return to the login page itself.
  if (raw === "/login" || raw.startsWith("/login?") || raw.startsWith("/login/")) return "/assistant";
  return raw;
}

export const clearAuth = (reason?: ClearAuthReason): void => {
  if (typeof window === "undefined") return;

  // Idempotent: only one redirect per page lifecycle.
  if (_clearAuthInProgress) return;
  _clearAuthInProgress = true;

  window.sessionStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(TOKEN_KEY);
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
    const token = getAuthToken();
    if (!token) {
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
// Cross-tab logout sync via BroadcastChannel
// ---------------------------------------------------------------------------

const LOGOUT_CHANNEL_NAME = "noa:auth";
const LOGOUT_MESSAGE_TYPE = "noa:logout";

let _logoutListenerInitialized = false;

function broadcastLogout(reason?: ClearAuthReason): void {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") return;
  try {
    const channel = new BroadcastChannel(LOGOUT_CHANNEL_NAME);
    channel.postMessage({ type: LOGOUT_MESSAGE_TYPE, reason: reason ?? null });
    channel.close();
  } catch {
    // BroadcastChannel not supported or blocked — silent fallback.
  }
}

function listenForLogoutBroadcast(): void {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") return;
  // Singleton guard: only one listener per module lifecycle.
  if (_logoutListenerInitialized) return;
  _logoutListenerInitialized = true;

  try {
    const channel = new BroadcastChannel(LOGOUT_CHANNEL_NAME);
    channel.onmessage = (event: MessageEvent) => {
      try {
        const data = event.data as { type?: string; reason?: string } | undefined;
        if (data?.type !== LOGOUT_MESSAGE_TYPE) return;

        // Another tab logged out — clear local state and redirect.
        // Use raw storage clear + redirect instead of clearAuth()
        // to avoid re-broadcasting and to bypass the idempotency guard
        // (which may already be set in this tab's lifecycle).
        window.sessionStorage.removeItem(TOKEN_KEY);
        window.localStorage.removeItem(TOKEN_KEY);
        window.localStorage.removeItem(USER_KEY);

        const reason = data.reason === "session_expired" ? "session_expired" : undefined;
        const params = new URLSearchParams();
        if (reason) params.set("reason", reason);
        const qs = params.toString();
        window.location.href = qs ? `/login?${qs}` : "/login";
      } catch {
        // Swallow handler errors to avoid breaking the event loop.
      }
    };
  } catch {
    // Silent fallback.
  }
}

// Initialize listener on module load (client-side only).
listenForLogoutBroadcast();
