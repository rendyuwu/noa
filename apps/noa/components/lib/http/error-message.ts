import { ApiError } from "./fetch-client";

export function toErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    return error.detail || fallback;
  }

  if (error instanceof Error) {
    return error.message || fallback;
  }

  return fallback;
}
