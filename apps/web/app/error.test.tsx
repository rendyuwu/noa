import type { ComponentType } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const reportClientError = vi.fn();

vi.mock("@/components/lib/error-reporting", () => ({
  reportClientError: (...args: unknown[]) => reportClientError(...args),
}));

type GlobalErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

const loadGlobalError = async (): Promise<ComponentType<GlobalErrorProps>> => {
  const modulePath = "./error";
  const module = await import(/* @vite-ignore */ modulePath);
  return module.default as ComponentType<GlobalErrorProps>;
};

describe("app/error", () => {
  beforeEach(() => {
    reportClientError.mockReset();
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders fallback copy and retries when Try again is clicked", async () => {
    const GlobalError = await loadGlobalError();
    const reset = vi.fn();

    render(<GlobalError error={new Error("boom")} reset={reset} />);

    expect(screen.getByText("Something went wrong")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("reports route-level render failures through the client error adapter", async () => {
    const GlobalError = await loadGlobalError();
    const error = Object.assign(new Error("boom"), { digest: "digest-123" });

    render(<GlobalError error={error} reset={vi.fn()} />);

    expect(console.error).toHaveBeenCalledWith(error);
    expect(reportClientError).toHaveBeenCalledWith(error, {
      digest: "digest-123",
      source: "app.error-boundary",
    });
  });
});
