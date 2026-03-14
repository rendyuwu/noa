"use client";

import { getAuthToken, clearAuth } from "@/components/lib/auth-store";

type ErrorPayload = {
  detail?: unknown;
  error_code?: unknown;
  request_id?: unknown;
  errorCode?: unknown;
  requestId?: unknown;
};

const asString = (value: unknown): string | undefined => {
  return typeof value === "string" && value.length > 0 ? value : undefined;
};

export const getApiUrl = (): string => {
  // Always use same-origin API routes from the browser.
  return "/api";
};

export class ApiError extends Error {
  status: number;
  detail: string;
  errorCode?: string;
  requestId?: string;

  constructor(
    status: number,
    detail: string,
    options: { errorCode?: string; requestId?: string } = {},
  ) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.errorCode = options.errorCode;
    this.requestId = options.requestId;
  }
}

export const fetchWithAuth = async (path: string, init: RequestInit = {}): Promise<Response> => {
  const rawPath = path.trim();
  const isAbsolute = /^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(rawPath) || rawPath.startsWith("//");
  if (isAbsolute) {
    throw new Error(
      `fetchWithAuth expects a path (e.g. "/api/foo"), but received an absolute URL: ${path}`
    );
  }

  const token = getAuthToken();
  const headers = new Headers(init.headers ?? {});
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }

  const normalizedPath = rawPath.startsWith("/") ? rawPath : `/${rawPath}`;
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
  const payload = (await response.json().catch(() => ({}))) as ErrorPayload;
  if (!response.ok) {
    const detail = asString(payload?.detail) ?? `Request failed (${response.status})`;
    const errorCode = asString(payload?.error_code) ?? asString(payload?.errorCode);
    const requestId =
      asString(payload?.request_id) ??
      asString(payload?.requestId) ??
      asString(response.headers.get("x-request-id"));

    throw new ApiError(response.status, detail, {
      errorCode,
      requestId,
    });
  }
  return payload as T;
};
