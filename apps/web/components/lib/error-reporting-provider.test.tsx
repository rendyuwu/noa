import type { ComponentType, ReactNode } from "react";
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const reportClientError = vi.fn();

vi.mock("./error-reporting", () => ({
  reportClientError: (...args: unknown[]) => reportClientError(...args),
}));

const loadErrorReportingProvider = async (): Promise<
  ComponentType<{ children?: ReactNode }>
> => {
  const modulePath = "./error-reporting-provider";
  const module = await import(/* @vite-ignore */ modulePath);
  return module.ErrorReportingProvider as ComponentType<{ children?: ReactNode }>;
};

const createWindowErrorEvent = (
  error: Error,
  options: { filename?: string } = {},
): ErrorEvent => {
  const event = new Event("error");
  Object.defineProperty(event, "error", { value: error });
  Object.defineProperty(event, "filename", { value: options.filename ?? "" });
  return event as ErrorEvent;
};

const createWindowMessageErrorEvent = (
  message: string,
  options: { filename?: string; lineno?: number; colno?: number } = {},
): ErrorEvent => {
  const event = new Event("error");
  Object.defineProperty(event, "message", { value: message });
  Object.defineProperty(event, "filename", { value: options.filename ?? "" });
  Object.defineProperty(event, "lineno", { value: options.lineno ?? 0 });
  Object.defineProperty(event, "colno", { value: options.colno ?? 0 });
  return event as ErrorEvent;
};

const createUnhandledRejectionEvent = (reason: unknown): PromiseRejectionEvent => {
  const event = new Event("unhandledrejection");
  Object.defineProperty(event, "reason", { value: reason });
  return event as PromiseRejectionEvent;
};

describe("ErrorReportingProvider", () => {
  beforeEach(() => {
    reportClientError.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("registers global listeners on mount and removes them on cleanup", async () => {
    const addEventListenerSpy = vi.spyOn(window, "addEventListener");
    const removeEventListenerSpy = vi.spyOn(window, "removeEventListener");
    const ErrorReportingProvider = await loadErrorReportingProvider();

    const { unmount } = render(
      <ErrorReportingProvider>
        <div>child</div>
      </ErrorReportingProvider>,
    );

    const errorHandler = addEventListenerSpy.mock.calls.find(([type]) => type === "error")?.[1];
    const rejectionHandler = addEventListenerSpy.mock.calls.find(
      ([type]) => type === "unhandledrejection",
    )?.[1];

    expect(errorHandler).toEqual(expect.any(Function));
    expect(rejectionHandler).toEqual(expect.any(Function));

    unmount();

    expect(removeEventListenerSpy).toHaveBeenCalledWith("error", errorHandler);
    expect(removeEventListenerSpy).toHaveBeenCalledWith(
      "unhandledrejection",
      rejectionHandler,
    );
  });

  it("forwards browser error and unhandled rejection failures through the adapter", async () => {
    const ErrorReportingProvider = await loadErrorReportingProvider();
    const error = new Error("window boom");
    const rejectionReason = new Error("promise boom");

    render(
      <ErrorReportingProvider>
        <div>child</div>
      </ErrorReportingProvider>,
    );

    window.dispatchEvent(createWindowErrorEvent(error));
    window.dispatchEvent(createUnhandledRejectionEvent(rejectionReason));

    expect(reportClientError).toHaveBeenNthCalledWith(1, error, {
      source: "window.error",
    });
    expect(reportClientError).toHaveBeenNthCalledWith(2, rejectionReason, {
      source: "window.unhandledrejection",
    });
  });

  it("falls back to the browser error message when the event has no error object", async () => {
    const ErrorReportingProvider = await loadErrorReportingProvider();

    render(
      <ErrorReportingProvider>
        <div>child</div>
      </ErrorReportingProvider>,
    );

    window.dispatchEvent(createWindowMessageErrorEvent("script load failed"));

    expect(reportClientError).toHaveBeenCalledWith(new Error("script load failed"), {
      source: "window.error",
    });
  });

  it("forwards browser metadata when reporting fallback window errors", async () => {
    const ErrorReportingProvider = await loadErrorReportingProvider();

    render(
      <ErrorReportingProvider>
        <div>child</div>
      </ErrorReportingProvider>,
    );

    window.dispatchEvent(
      createWindowMessageErrorEvent("script load failed", {
        filename: "/static/chunks/app.js",
        lineno: 42,
        colno: 7,
      }),
    );

    expect(reportClientError).toHaveBeenCalledWith(new Error("script load failed"), {
      source: "window.error",
      filename: "/static/chunks/app.js",
      lineno: 42,
      colno: 7,
    });
  });

  it("ignores generic script error events when the browser provides no error object", async () => {
    const ErrorReportingProvider = await loadErrorReportingProvider();

    render(
      <ErrorReportingProvider>
        <div>child</div>
      </ErrorReportingProvider>,
    );

    window.dispatchEvent(createWindowMessageErrorEvent("Script error."));

    expect(reportClientError).not.toHaveBeenCalled();
  });

  it("ignores extension error events when the browser provides no error object", async () => {
    const ErrorReportingProvider = await loadErrorReportingProvider();

    render(
      <ErrorReportingProvider>
        <div>child</div>
      </ErrorReportingProvider>,
    );

    window.dispatchEvent(
      createWindowMessageErrorEvent("Extension context invalidated.", {
        filename: "chrome-extension://extension-id/content-script.js",
      }),
    );

    expect(reportClientError).not.toHaveBeenCalled();
  });

  it("ignores extension error events even when the browser provides an error object", async () => {
    const ErrorReportingProvider = await loadErrorReportingProvider();

    render(
      <ErrorReportingProvider>
        <div>child</div>
      </ErrorReportingProvider>,
    );

    window.dispatchEvent(
      createWindowErrorEvent(new Error("Extension context invalidated."), {
        filename: "chrome-extension://extension-id/content-script.js",
      }),
    );

    expect(reportClientError).not.toHaveBeenCalled();
  });
});
