"use client";

import type { AuthUser } from "./types";
import { sanitizeReturnTo } from "./return-to";

const USER_KEY = "noa.user";

function isBrowser() {
  return typeof window !== "undefined";
}

export function getAuthUser(): AuthUser | null {
  if (!isBrowser()) {
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
}

export function setAuthUser(user: AuthUser | null) {
  if (!isBrowser()) {
    return;
  }

  if (user === null) {
    window.localStorage.removeItem(USER_KEY);
    return;
  }

  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearAuth(options: { returnTo?: string; redirect?: boolean } = {}) {
  if (!isBrowser()) {
    return;
  }

  window.localStorage.removeItem(USER_KEY);
  void fetch("/api/auth/logout", {
    credentials: "same-origin",
    keepalive: true,
    method: "POST",
  }).catch(() => {});

  if (options.redirect === false) {
    return;
  }

  const returnTo = sanitizeReturnTo(options.returnTo ?? window.location.pathname + window.location.search + window.location.hash);
  window.location.href = `/login?returnTo=${encodeURIComponent(returnTo)}`;
}
