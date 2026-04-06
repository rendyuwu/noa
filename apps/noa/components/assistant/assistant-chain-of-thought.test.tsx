import { render, screen } from "@testing-library/react";
import { createContext, useContext, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

const ChainOfThoughtScopeContext = createContext(false);

vi.mock("@assistant-ui/react", async () => {
  return {
    ChainOfThoughtPrimitive: {
      Root: ({ children, ...props }: any) => <div {...props}>{children}</div>,
      AccordionTrigger: ({ children, ...props }: any) => {
        if (!useContext(ChainOfThoughtScopeContext)) {
          throw new Error('The current scope does not have a "chainOfThought" property.');
        }

        return <button type="button" {...props}>{children}</button>;
      },
      Parts: ({ components }: any) => (
        <div>
          <components.Reasoning text="Tracing the WHM validation request." />
          <components.tools.Fallback toolName="whm_validate_server" status={{ type: "running" }} />
        </div>
      ),
    },
  };
});

vi.mock("./assistant-tool-ui", () => ({
  ToolFallback: ({ toolName }: any) => <div>{toolName}</div>,
}));

import { AssistantChainOfThought } from "./assistant-chain-of-thought";

describe("AssistantChainOfThought", () => {
  it("throws when rendered outside the assistant-ui chain-of-thought scope", () => {
    expect(() => render(<AssistantChainOfThought />)).toThrow('The current scope does not have a "chainOfThought" property.');
  });

  it("renders when wrapped in the assistant-ui chain-of-thought scope", () => {
    const Wrapped = ({ children }: { children: ReactNode }) => (
      <ChainOfThoughtScopeContext.Provider value={true}>{children}</ChainOfThoughtScopeContext.Provider>
    );

    render(
      <Wrapped>
        <AssistantChainOfThought />
      </Wrapped>,
    );

    expect(screen.getByRole("button", { name: /Thinking/ })).toBeInTheDocument();
    expect(screen.getByText("Tracing the WHM validation request.")).toBeInTheDocument();
    expect(screen.getByText("whm_validate_server")).toBeInTheDocument();
  });
});
