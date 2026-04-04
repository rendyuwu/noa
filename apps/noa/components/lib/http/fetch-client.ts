"use client";

import { clearAuth } from "@/components/lib/auth/auth-storage";
import { reportClientError } from "@/components/lib/observability/error-reporting";

type ErrorPayload = {
  detail?: unknown;
  error_code?: unknown;
  request_id?: unknown;
  errorCode?: unknown;
  requestId?: unknown;
};

const asString = (value: unknown) =>
  typeof value === "string" && value.length > 0 ? value : undefined;

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

export function getApiUrl() {
  return "/api";
}

export function getCsrfToken() {
  if (typeof document === "undefined") {
    return null;
  }

  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("noa_csrf="));

  if (!cookie) {
    return null;
  }

  const token = cookie.slice("noa_csrf=".length);
  return token.length > 0 ? decodeURIComponent(token) : null;
}

export async function fetchWithAuth(path: string, init: RequestInit = {}) {
  const rawPath = path.trim();
  const isAbsolute = /^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(rawPath) || rawPath.startsWith("//");
  if (isAbsolute) {
    throw new Error(`fetchWithAuth expects a path, received absolute URL: ${path}`);
  }

  const normalizedPath = rawPath.startsWith("/") ? rawPath : `/${rawPath}`;
  const url = normalizedPath === "/api" || normalizedPath.startsWith("/api/") ? normalizedPath : `${getApiUrl()}${normalizedPath}`;

  const headers = new Headers(init.headers ?? {});
  const method = (init.method ?? "GET").toUpperCase();
  const csrfToken = getCsrfToken();
  if (csrfToken && ["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    headers.set("x-csrf-token", csrfToken);
  }

  let response: Response;

  try {
    response = await fetch(url, {
      ...init,
      credentials: "same-origin",
      headers,
    });
  } catch (error) {
    try {
      reportClientError(error, {
        method,
        path: url,
        source: "fetchWithAuth",
      });
    } catch {
      // Preserve the original fetch error.
    }
    throw error;
  }

  if (response.status === 401) {
    clearAuth();
  }

  return response;
}

export async function jsonOrThrow<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as ErrorPayload;

  if (!response.ok) {
    const detail = asString(payload.detail) ?? `Request failed (${response.status})`;
    const errorCode = asString(payload.error_code) ?? asString(payload.errorCode);
    const requestId =
      asString(payload.request_id) ??
      asString(payload.requestId) ??
      asString(response.headers.get("x-request-id"));

    const error = new ApiError(response.status, detail, {
      errorCode,
      requestId,
    });

    if (response.status === 0 || response.status >= 500) {
      reportClientError(error, {
        errorCode,
        requestId,
        source: "jsonOrThrow",
        status: response.status,
      });
    }

    throw error;
  }

  return payload as T;
}
