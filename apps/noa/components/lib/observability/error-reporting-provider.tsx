"use client";

import type { ReactNode } from "react";
import { useEffect } from "react";

import { reportClientError } from "@/components/lib/observability/error-reporting";

const GENERIC_WINDOW_ERROR_MESSAGES = new Set(["Script error."]);
const EXTENSION_URL_PREFIXES = [
  "chrome-extension://",
  "moz-extension://",
  "safari-extension://",
  "safari-web-extension://",
];

const isExtensionWindowErrorEvent = (event: ErrorEvent): boolean => {
  const filename = event.filename.trim();

  return filename.length > 0 && EXTENSION_URL_PREFIXES.some((prefix) => filename.startsWith(prefix));
};

const getFallbackWindowErrorContext = (event: ErrorEvent) => {
  return {
    source: "window.error",
    ...(event.filename.trim().length > 0 ? { filename: event.filename } : {}),
    ...(event.lineno > 0 ? { lineno: event.lineno } : {}),
    ...(event.colno > 0 ? { colno: event.colno } : {}),
  };
};

const getFallbackWindowError = (event: ErrorEvent): Error | undefined => {
  const message = event.message.trim();
  if (message.length === 0 || GENERIC_WINDOW_ERROR_MESSAGES.has(message)) {
    return undefined;
  }

  return new Error(message);
};

export function ErrorReportingProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    const handleError = (event: ErrorEvent) => {
      if (isExtensionWindowErrorEvent(event)) {
        return;
      }

      if (event.error != null) {
        reportClientError(event.error, { source: "window.error" });
        return;
      }

      const fallbackError = getFallbackWindowError(event);
      if (fallbackError !== undefined) {
        reportClientError(fallbackError, getFallbackWindowErrorContext(event));
      }
    };

    const handleUnhandledRejection = (event: PromiseRejectionEvent) => {
      reportClientError(event.reason, { source: "window.unhandledrejection" });
    };

    window.addEventListener("error", handleError);
    window.addEventListener("unhandledrejection", handleUnhandledRejection);

    return () => {
      window.removeEventListener("error", handleError);
      window.removeEventListener("unhandledrejection", handleUnhandledRejection);
    };
  }, []);

  return <>{children}</>;
}
