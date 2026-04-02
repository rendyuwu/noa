"use client";

import type { AuthUser } from "./types";
import { sanitizeReturnTo } from "./return-to";

const TOKEN_KEY = "noa.jwt";
const USER_KEY = "noa.user";

function isBrowser() {
  return typeof window !== "undefined";
}

export function getAuthToken(): string | null {
  if (!isBrowser()) {
    return null;
  }

  const sessionToken = window.sessionStorage.getItem(TOKEN_KEY);
  if (sessionToken) {
    return sessionToken;
  }

  const legacyToken = window.localStorage.getItem(TOKEN_KEY);
  if (!legacyToken) {
    return null;
  }

  window.sessionStorage.setItem(TOKEN_KEY, legacyToken);
  window.localStorage.removeItem(TOKEN_KEY);
  return legacyToken;
}

export function setAuthToken(token: string) {
  if (!isBrowser()) {
    return;
  }

  window.sessionStorage.setItem(TOKEN_KEY, token);
  window.localStorage.removeItem(TOKEN_KEY);
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

  window.sessionStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);

  if (options.redirect === false) {
    return;
  }

  const returnTo = sanitizeReturnTo(options.returnTo ?? window.location.pathname + window.location.search + window.location.hash);
  window.location.href = `/login?returnTo=${encodeURIComponent(returnTo)}`;
}
