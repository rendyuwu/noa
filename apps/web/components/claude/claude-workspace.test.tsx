import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@radix-ui/react-dialog", async () => {
  const React = await import("react");

  type WrapperProps = { children?: React.ReactNode };

  return {
    Root: ({ children }: WrapperProps) => <div>{children}</div>,
    Portal: ({ children }: WrapperProps) => <div>{children}</div>,
    Overlay: (props: React.ComponentPropsWithoutRef<"div">) => <div {...props} />,
    Content: (props: React.ComponentPropsWithoutRef<"div">) => <div {...props} />,
    Title: (props: React.ComponentPropsWithoutRef<"h2">) => <h2 {...props} />,
    Close: ({ children }: WrapperProps) => <>{children}</>,
  };
});

vi.mock("@/components/claude/claude-thread", () => ({
  ClaudeThread: () => <div data-testid="claude-thread" />,
}));

vi.mock("@/components/claude/claude-thread-list", () => ({
  ClaudeThreadList: () => <div data-testid="claude-thread-list" />,
}));

vi.mock("@/components/claude/request-approval-tool-ui", () => ({
  RequestApprovalToolUI: () => <div data-testid="request-approval-tool-ui" />,
}));

vi.mock("@/components/lib/auth-store", () => ({
  useRequireAuth: () => true,
}));

vi.mock("@/components/lib/runtime-provider", async () => {
  const React = await import("react");
  return {
    NoaAssistantRuntimeProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  };
});

import AssistantPage from "@/app/(app)/assistant/page";
import { ClaudeWorkspace } from "./claude-workspace";

describe("/assistant full-bleed shell", () => {
  beforeEach(() => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the assistant route as a full-bleed surface (no page-shell padding)", () => {
    render(<AssistantPage />);

    const main = screen.getByRole("main");
    expect(main).not.toHaveClass("page-shell");
    expect(main).toHaveClass("min-h-dvh");
    expect(main).toHaveClass("bg-[#F5F5F0]");
  });

  it("renders ClaudeWorkspace without the framed card shell", () => {
    const { container } = render(<ClaudeWorkspace />);

    const section = container.querySelector("section");
    expect(section).not.toBeNull();

    expect(section!).toHaveClass("h-dvh");
    expect(section!).not.toHaveClass("rounded-2xl");
    expect(section!).not.toHaveClass("border");
    expect(section!.className).not.toMatch(/\bshadow/);
  });
});
