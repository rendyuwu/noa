import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

const themeProviderMock = vi.fn(({ children }: { children: ReactNode }) => (
  <div data-testid="theme-provider">{children}</div>
));
const toasterMock = vi.fn(() => <div data-testid="toaster" />);
const geistMock = vi.fn(() => ({ variable: "--font-geist-sans" }));
const geistMonoMock = vi.fn(() => ({ variable: "--font-geist-mono" }));
const newsreaderMock = vi.fn(() => ({ variable: "--font-newsreader" }));

vi.mock("next/font/google", () => ({
  Geist: (...args: unknown[]) => geistMock(...args),
  Geist_Mono: (...args: unknown[]) => geistMonoMock(...args),
  Newsreader: (...args: unknown[]) => newsreaderMock(...args),
}));

vi.mock("@/components/noa/theme-provider", () => ({
  ThemeProvider: (props: { children: React.ReactNode }) => themeProviderMock(props),
}));

vi.mock("@/components/ui/sonner", () => ({
  Toaster: () => toasterMock(),
}));

const loadRootLayout = async () => {
  const module = await import("./layout");
  return module.default;
};

describe("RootLayout", () => {
  it("applies the editorial font and theme shell", async () => {
    const RootLayout = await loadRootLayout();

    render(
      <RootLayout>
        <main>Content</main>
      </RootLayout>,
      { container: document.documentElement },
    );

    expect(document.body.className).toContain("--font-newsreader");
    expect(newsreaderMock).toHaveBeenCalledWith(
      expect.objectContaining({
        subsets: ["latin"],
        variable: "--font-newsreader",
        weight: ["400", "500"],
      }),
    );
    expect(themeProviderMock).toHaveBeenCalledWith(
      expect.objectContaining({
        attribute: "class",
        defaultTheme: "light",
        enableSystem: true,
      }),
    );
    expect(screen.getByTestId("toaster")).toBeInTheDocument();
  });
});
