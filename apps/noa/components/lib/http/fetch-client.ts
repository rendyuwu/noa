"use client";

import { clearAuth, getAuthToken } from "@/components/lib/auth/auth-storage";
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

export async function fetchWithAuth(path: string, init: RequestInit = {}) {
  const rawPath = path.trim();
  const isAbsolute = /^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(rawPath) || rawPath.startsWith("//");
  if (isAbsolute) {
    throw new Error(`fetchWithAuth expects a path, received absolute URL: ${path}`);
  }

  const normalizedPath = rawPath.startsWith("/") ? rawPath : `/${rawPath}`;
  const url = normalizedPath === "/api" || normalizedPath.startsWith("/api/") ? normalizedPath : `${getApiUrl()}${normalizedPath}`;

  const headers = new Headers(init.headers ?? {});
  const token = getAuthToken();
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }

  let response: Response;

  try {
    response = await fetch(url, {
      ...init,
      headers,
    });
  } catch (error) {
    try {
      reportClientError(error);
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
        status: response.status,
      });
    }

    throw error;
  }

  return payload as T;
}
