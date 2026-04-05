import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  collapsed: false,
  hasSource: true,
}));

vi.mock("@assistant-ui/react", async () => {
  return {
    ChainOfThoughtPrimitive: {
      Root: ({ children, ...props }: any) => <div {...props}>{children}</div>,
      AccordionTrigger: ({ children, ...props }: any) => <button type="button" {...props}>{children}</button>,
      Parts: ({ components }: any) => (
        <div>
          <components.Reasoning text="Tracing the WHM validation request." />
          <components.tools.Fallback toolName="whm_validate_server" status={{ type: "running" }} />
        </div>
      ),
    },
    useAuiState: (selector: any) => selector(new Proxy(
      {},
      {
        get(_target, property) {
          if (property === "chainOfThought") {
            if (!mocks.hasSource) {
              throw new Error('The current scope does not have a "chainOfThought" property.');
            }

            return { collapsed: mocks.collapsed };
          }

          return undefined;
        },
      },
    )),
    useAui: () => ({
      chainOfThought: {
        source: mocks.hasSource ? {} : null,
      },
    }),
    AuiIf: ({ condition, children }: any) => (condition({ chainOfThought: { collapsed: mocks.collapsed } }) ? <>{children}</> : null),
  };
});

vi.mock("./assistant-tool-ui", () => ({
  ToolFallback: ({ toolName }: any) => <div>{toolName}</div>,
}));

import { AssistantChainOfThought } from "./assistant-chain-of-thought";

describe("AssistantChainOfThought", () => {
  beforeEach(() => {
    mocks.collapsed = false;
    mocks.hasSource = true;
  });

  it("renders the Thinking accordion with reasoning and tool parts inside chain-of-thought scope", () => {
    render(<AssistantChainOfThought />);

    expect(screen.getByRole("button", { name: /Thinking/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Tracing the WHM validation request.")).toBeInTheDocument();
    expect(screen.getByText("whm_validate_server")).toBeInTheDocument();
  });

  it("does not render when chain-of-thought scope is unavailable", () => {
    mocks.hasSource = false;

    expect(() => render(<AssistantChainOfThought />)).not.toThrow();
    expect(screen.queryByRole("button", { name: /Thinking/ })).not.toBeInTheDocument();
  });
});
