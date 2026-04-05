import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  collapsed: false,
}));

vi.mock("@assistant-ui/react", () => ({
  ChainOfThoughtPrimitive: {
    Root: ({ children }: any) => <div>{children}</div>,
    AccordionTrigger: ({ children, ...props }: any) => <button type="button" {...props}>{children}</button>,
    Parts: ({ components }: any) => (
      <div>
        <components.Reasoning text="Tracing the WHM validation request." />
        <components.tools.Fallback toolName="whm_validate_server" status={{ type: "running" }} />
      </div>
    ),
  },
  useAuiState: (selector: any) => selector({ chainOfThought: { collapsed: mocks.collapsed } }),
  useMessage: () => ({
    content: [
      { type: "reasoning", text: "Tracing the WHM validation request." },
      { type: "tool-call", toolName: "whm_validate_server" },
    ],
  }),
}));

vi.mock("./assistant-tool-ui", () => ({
  ToolFallback: ({ toolName }: any) => <div>{toolName}</div>,
}));

import { AssistantChainOfThought } from "./assistant-chain-of-thought";

describe("AssistantChainOfThought", () => {
  beforeEach(() => {
    mocks.collapsed = false;
  });

  it("renders the Thinking accordion with reasoning and tool parts", () => {
    render(<AssistantChainOfThought />);

    expect(screen.getByRole("button", { name: /Thinking/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Tracing the WHM validation request.")).toBeInTheDocument();
    expect(screen.getByText("whm_validate_server")).toBeInTheDocument();
  });
});
