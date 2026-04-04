import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  setTheme: vi.fn(),
  theme: "light" as "light" | "dark",
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({
    theme: mocks.theme,
    resolvedTheme: mocks.theme,
    setTheme: (...args: unknown[]) => mocks.setTheme(...args),
  }),
}));

import { ThemeToggle } from "./theme-toggle";

describe("ThemeToggle", () => {
  beforeEach(() => {
    mocks.setTheme.mockReset();
    mocks.theme = "light";
  });

  it("switches from light to dark mode", async () => {
    const user = userEvent.setup();

    render(<ThemeToggle />);

    const button = await screen.findByRole("button", { name: "Switch to dark mode" });

    await user.click(button);

    expect(mocks.setTheme).toHaveBeenCalledWith("dark");
  });
});
