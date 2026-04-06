import { ApiError } from "./fetch-helper";

const PENDING_APPROVAL_MESSAGE = "Your account is pending approval. Ask an admin to enable it.";
const SAFE_FALLBACK_MESSAGE = "Unable to reach API";

type ErrorLike = {
  detail?: unknown;
  errorCode?: unknown;
  status?: unknown;
};

const isErrorLike = (error: unknown): error is ErrorLike => {
  return typeof error === "object" && error !== null;
};

export const toUserMessage = (error: unknown, fallback = SAFE_FALLBACK_MESSAGE): string => {
  if (error instanceof ApiError) {
    if (error.errorCode === "user_pending_approval") {
      return PENDING_APPROVAL_MESSAGE;
    }

    return error.detail || fallback;
  }

  if (isErrorLike(error)) {
    if (error.errorCode === "user_pending_approval") {
      return PENDING_APPROVAL_MESSAGE;
    }

    if (typeof error.detail === "string" && error.detail.length > 0) {
      return error.detail;
    }
  }

  if (error instanceof TypeError) {
    return fallback;
  }

  if (error instanceof Error && error.message) {
    return fallback;
  }

  return fallback;
};
