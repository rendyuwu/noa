import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ClaudeToolFallback,
  ClaudeToolGroup,
} from "@/components/claude/request-approval-tool-ui";

describe("ClaudeToolFallback", () => {
  it("hides successful tools", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-1"
        status={{ type: "complete" }}
        result={{ time: "10:00" }}
        isError={false}
      />,
    );

    expect(screen.queryByText(/^Current time$/i)).not.toBeInTheDocument();
  });

  it("hides running tools", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-2"
        argsText='{"secret":"nope"}'
        isError={false}
      />,
    );

    expect(screen.queryByText(/^Current time$/i)).not.toBeInTheDocument();
  });

  it("hides requires-action tools", () => {
    render(
      <ClaudeToolFallback
        toolName="set_demo_flag"
        toolCallId="tool-call-3"
        status={{ type: "requires-action" }}
        isError={undefined}
      />,
    );

    expect(screen.queryByText(/requires-action/i)).not.toBeInTheDocument();
  });

  it("renders a compact row for failed tools", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-4"
        status={{ type: "incomplete" }}
        argsText='{"secret":"nope"}'
        result={{ time: "10:00" }}
        isError
      />,
    );

    expect(screen.getByText(/^Current time$/i)).toBeVisible();
    expect(screen.getByText(/^incomplete$/i)).toBeInTheDocument();
    expect(screen.getByText(/could not complete current time/i)).toBeVisible();
    expect(screen.queryByText(/secret/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/10:00/)).not.toBeInTheDocument();
  });

  it("does not render a wrapper when only hidden children exist", () => {
    const { container } = render(
      <ClaudeToolGroup>
        <ClaudeToolFallback
          toolName="get_current_time"
          toolCallId="tool-call-5"
          status={{ type: "complete" }}
          result={{ time: "10:00" }}
          isError={false}
        />
      </ClaudeToolGroup>,
    );

    expect(container.firstChild).toBeNull();
  });
});
