"use client";

import {
  AuthRedirectError,
  clearAuth,
  isClearAuthInProgress,
} from "@/components/lib/auth-store";

import { reportClientError } from "@/components/lib/observability/error-reporting";

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

const shouldReportApiFailure = (status: number): boolean => {
  return status === 0 || status >= 500;
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

  // If a logout redirect is already in flight, short-circuit immediately.
  if (isClearAuthInProgress()) {
    throw new AuthRedirectError();
  }

  const headers = new Headers(init.headers ?? {});

  const normalizedPath = rawPath.startsWith("/") ? rawPath : `/${rawPath}`;
  const url =
    normalizedPath === "/api" || normalizedPath.startsWith("/api/")
      ? normalizedPath
      : `${getApiUrl()}${normalizedPath}`;

  let response: Response;

  try {
    response = await fetch(url, {
      ...init,
      headers,
      credentials: "include",
    });
  } catch (error) {
    try {
      reportClientError(error);
    } catch {}
    throw error;
  }

  if (response.status === 401) {
    clearAuth("session_expired");
    throw new AuthRedirectError("session_expired");
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

    const error = new ApiError(response.status, detail, {
      errorCode,
      requestId,
    });

    if (shouldReportApiFailure(response.status)) {
      reportClientError(error, {
        errorCode,
        requestId,
        status: response.status,
      });
    }

    throw error;
  }
  return payload as T;
};
