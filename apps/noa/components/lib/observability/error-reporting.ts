export type ErrorContext = Record<string, unknown>;

const isEnabled = () => process.env.NEXT_PUBLIC_ERROR_REPORTING_ENABLED === "true";

export function reportClientError(error: unknown, context: ErrorContext = {}) {
  if (!isEnabled()) {
    return;
  }

  console.error("[noa-client-error]", error, context);
}
