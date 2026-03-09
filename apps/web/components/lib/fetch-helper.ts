"use client";

import { getAuthToken, clearAuth } from "@/components/lib/auth-store";

export const getApiUrl = (): string => {
  // Always use same-origin API routes from the browser.
  return "/api";
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export const fetchWithAuth = async (path: string, init: RequestInit = {}): Promise<Response> => {
  if (path.includes("://")) {
    throw new Error(
      `fetchWithAuth expects a path (e.g. "/api/foo"), but received an absolute URL: ${path}`
    );
  }

  const token = getAuthToken();
  const headers = new Headers(init.headers ?? {});
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }

  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url =
    normalizedPath === "/api" || normalizedPath.startsWith("/api/")
      ? normalizedPath
      : `${getApiUrl()}${normalizedPath}`;

  const response = await fetch(url, {
    ...init,
    headers,
  });

  if (response.status === 401) {
    clearAuth();
  }

  return response;
};

export const jsonOrThrow = async <T>(response: Response): Promise<T> => {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, payload?.detail ?? `Request failed (${response.status})`);
  }
  return payload as T;
};
