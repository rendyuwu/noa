import * as Sentry from "@sentry/nextjs";

import { ApiError } from "@/components/lib/fetch-helper";

type ReportExtraValue = boolean | number | string;

export type ReportContext = Record<string, ReportExtraValue | null | undefined>;

const HANDLED_API_ERROR_CODES = new Set([
  "invalid_credentials",
  "missing_bearer_token",
  "invalid_token",
  "user_pending_approval",
  "admin_access_required",
  "tool_access_denied",
  "admin_user_not_found",
  "thread_not_found",
  "whm_server_not_found",
  "tool_call_not_found",
  "action_request_not_found",
  "last_active_admin",
  "self_deactivate_admin",
  "whm_server_name_exists",
  "action_request_already_decided",
  "request_validation_error",
]);
const CANCELED_ERROR_CODES = new Set(["ERR_CANCELED"]);
const CANCELED_ERROR_NAMES = new Set(["AbortError"]);
const CANCELED_MESSAGE_PATTERN = /\b(abort(?:ed)?|cancel(?:ed|led)?)\b/i;

let isSentryInitialized = false;

const reportedErrors = new WeakSet<Error>();

const asNonEmptyString = (value: unknown): string | undefined => {
  return typeof value === "string" && value.trim().length > 0 ? value : undefined;
};

const asFiniteNumber = (value: unknown): number | undefined => {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
};

const getErrorReportingConfig = ():
  | { dsn: string; environment?: string }
  | undefined => {
  const dsn = asNonEmptyString(process.env.NEXT_PUBLIC_ERROR_REPORTING_DSN);
  if (process.env.NEXT_PUBLIC_ERROR_REPORTING_ENABLED !== "true" || dsn === undefined) {
    return undefined;
  }

  const environment = asNonEmptyString(process.env.NEXT_PUBLIC_ERROR_REPORTING_ENVIRONMENT);

  return environment === undefined ? { dsn } : { dsn, environment };
};

const addExtra = (
  extras: Record<string, ReportExtraValue>,
  key: string,
  value: unknown,
) => {
  if (typeof value === "boolean") {
    extras[key] = value;
    return;
  }

  const stringValue = asNonEmptyString(value);
  if (stringValue !== undefined) {
    extras[key] = stringValue;
    return;
  }

  const numberValue = asFiniteNumber(value);
  if (numberValue !== undefined) {
    extras[key] = numberValue;
  }
};

const normalizeError = (error: unknown): Error => {
  if (error instanceof Error) {
    return error;
  }

  const message =
    asNonEmptyString(error) ??
    (typeof error === "object" && error !== null && "message" in error
      ? asNonEmptyString((error as { message?: unknown }).message)
      : undefined);

  return new Error(message ?? "Unknown client error");
};

const asErrorField = (error: unknown, key: "code" | "message" | "name"): string | undefined => {
  if (typeof error !== "object" || error === null || !(key in error)) {
    return undefined;
  }

  return asNonEmptyString((error as Record<typeof key, unknown>)[key]);
};

const isCanceledError = (error: unknown): boolean => {
  const name = asErrorField(error, "name");
  if (name !== undefined && CANCELED_ERROR_NAMES.has(name)) {
    return true;
  }

  const code = asErrorField(error, "code");
  if (code !== undefined && CANCELED_ERROR_CODES.has(code)) {
    return true;
  }

  const message = asErrorField(error, "message") ?? asNonEmptyString(error);
  return message !== undefined && CANCELED_MESSAGE_PATTERN.test(message);
};

const isHandledApiError = (error: unknown): error is ApiError => {
  return error instanceof ApiError && HANDLED_API_ERROR_CODES.has(error.errorCode ?? "");
};

const ensureSentryInitialized = (): boolean => {
  if (isSentryInitialized) {
    return true;
  }

  const config = getErrorReportingConfig();
  if (config === undefined) {
    return false;
  }

  try {
    Sentry.init({
      ...config,
      defaultIntegrations: false,
    });
    isSentryInitialized = true;
    return true;
  } catch {
    return false;
  }
};

const getPathname = (): string | undefined => {
  if (typeof window === "undefined") {
    return undefined;
  }

  return asNonEmptyString(window.location.pathname);
};

export const isErrorReportingEnabled = (): boolean => {
  return getErrorReportingConfig() !== undefined;
};

export const shouldReportClientError = (error: unknown): boolean => {
  if (isCanceledError(error)) {
    return false;
  }

  if (isHandledApiError(error)) {
    return false;
  }

  return true;
};

export const buildReportExtras = (
  error: unknown,
  context: ReportContext = {},
): Record<string, ReportExtraValue> => {
  const extras: Record<string, ReportExtraValue> = {};

  for (const [key, value] of Object.entries(context)) {
    addExtra(extras, key, value);
  }

  if (error instanceof ApiError) {
    addExtra(extras, "requestId", context.requestId ?? error.requestId);
    addExtra(extras, "errorCode", context.errorCode ?? error.errorCode);
    addExtra(extras, "status", context.status ?? error.status);
  } else {
    addExtra(extras, "requestId", context.requestId);
    addExtra(extras, "errorCode", context.errorCode);
    addExtra(extras, "status", context.status);
  }

  addExtra(extras, "pathname", context.pathname ?? getPathname());

  return extras;
};

export const reportClientError = (error: unknown, context: ReportContext = {}) => {
  if (!shouldReportClientError(error)) {
    return;
  }

  const normalizedError = normalizeError(error);
  if (reportedErrors.has(normalizedError)) {
    return;
  }

  if (!ensureSentryInitialized()) {
    return;
  }

  try {
    Sentry.captureException(normalizedError, {
      extra: buildReportExtras(error, context),
    });
    reportedErrors.add(normalizedError);
  } catch {}
};
