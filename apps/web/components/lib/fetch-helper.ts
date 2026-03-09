"use client";

import { getAuthToken, clearAuth } from "@/components/lib/auth-store";

export const getApiUrl = (): string => {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export const fetchWithAuth = async (path: string, init: RequestInit = {}): Promise<Response> => {
  const token = getAuthToken();
  const headers = new Headers(init.headers ?? {});
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${getApiUrl()}${path}`, {
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
