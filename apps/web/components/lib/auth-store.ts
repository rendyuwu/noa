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

export const getAuthToken = (): string | null => {
  if (typeof window === "undefined") {
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

export const clearAuth = (): void => {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
  window.location.href = "/login";
};

export const useRequireAuth = (): boolean => {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    setReady(true);
  }, [router]);

  return ready;
};
