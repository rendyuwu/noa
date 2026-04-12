import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Alert } from "./alert";
import { Badge } from "./badge";
import { Button } from "./button";
import { Input } from "./input";
import { Separator } from "./separator";
import { Skeleton } from "./skeleton";
import { Toaster } from "./sonner";

const { sonnerMock, useThemeMock } = vi.hoisted(() => ({
  sonnerMock: vi.fn((props: any) => <div data-testid="sonner" data-theme={props?.theme} />),
  useThemeMock: vi.fn(() => ({ theme: "system" })),
}));

vi.mock("next-themes", () => ({
  useTheme: () => useThemeMock(),
}));

vi.mock("sonner", () => ({
  Toaster: (props: unknown) => sonnerMock(props),
}));

describe("editorial shared controls", () => {
  it("uses warmer button treatments across core variants", () => {
    const { container: defaultContainer } = render(<Button>Primary</Button>);
    const { container: secondaryContainer } = render(<Button variant="secondary">Secondary</Button>);
    const { container: outlineContainer } = render(<Button variant="outline">Outline</Button>);
    const { container: ghostContainer } = render(<Button variant="ghost">Ghost</Button>);
    const { container: linkContainer } = render(<Button variant="link">Link</Button>);

    expect(defaultContainer.firstElementChild).toHaveClass("rounded-xl");
    expect(defaultContainer.firstElementChild).toHaveClass("bg-primary");
    expect(defaultContainer.firstElementChild).toHaveClass("shadow-sm");

    expect(secondaryContainer.firstElementChild).toHaveClass("rounded-xl");
    expect(secondaryContainer.firstElementChild).toHaveClass("bg-secondary");
    expect(secondaryContainer.firstElementChild).toHaveClass("shadow-sm");

    expect(outlineContainer.firstElementChild).toHaveClass("rounded-xl");
    expect(outlineContainer.firstElementChild).toHaveClass("bg-card/80");
    expect(outlineContainer.firstElementChild).toHaveClass("border-border");

    expect(ghostContainer.firstElementChild).not.toHaveClass("shadow-sm");
    expect(linkContainer.firstElementChild).not.toHaveClass("shadow-sm");
  });

  it("uses the editorial contained input surface", () => {
    const { container } = render(<Input aria-label="Editorial input" />);

    expect(container.firstElementChild).toHaveClass("input");
    expect(container.firstElementChild).toHaveClass("text-base");
    expect(container.firstElementChild).toHaveClass("md:text-sm");
  });

  it("keeps badges rounded and editorial", () => {
    const { container } = render(<Badge>New</Badge>);

    expect(container.firstElementChild).toHaveClass("rounded-full");
    expect(container.firstElementChild).toHaveClass("text-[10px]");
    expect(container.firstElementChild).toHaveClass("uppercase");
    expect(container.firstElementChild).toHaveClass("font-semibold");
  });

  it("uses the larger contained alert surface", () => {
    const { container } = render(<Alert>Editorial alert</Alert>);

    expect(container.firstElementChild).toHaveClass("rounded-2xl");
    expect(container.firstElementChild).toHaveClass("bg-card/80");
    expect(container.firstElementChild).toHaveClass("shadow-sm");
  });

  it("uses the updated skeleton rounding", () => {
    const { container } = render(<Skeleton />);

    expect(container.firstElementChild).toHaveClass("rounded-xl");
  });

  it("softens separators with the editorial tone", () => {
    const { container } = render(<Separator />);

    expect(container.firstElementChild).toHaveClass("bg-border/60");
  });

  it("keeps toaster surfaces warm and contained", () => {
    render(<Toaster />);

    expect(sonnerMock).toHaveBeenCalledWith(
      expect.objectContaining({
        toastOptions: expect.objectContaining({
          classNames: expect.objectContaining({
            toast: expect.stringContaining("rounded-2xl"),
          }),
        }),
      }),
    );
  });
});
