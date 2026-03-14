import type { ComponentType } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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
  it("renders fallback copy and retries when Try again is clicked", async () => {
    const GlobalError = await loadGlobalError();
    const reset = vi.fn();

    render(<GlobalError error={new Error("boom")} reset={reset} />);

    expect(screen.getByText("Something went wrong")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(reset).toHaveBeenCalledTimes(1);
  });
});
