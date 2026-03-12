import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ClaudeToolFallback } from "@/components/claude/request-approval-tool-ui";

describe("ClaudeToolFallback", () => {
  it("renders a compact activity line without raw args/result", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-1"
        status={{ type: "complete" }}
        argsText='{"secret":"nope"}'
        result={{ time: "10:00" }}
        isError={false}
      />,
    );

    expect(screen.getByText(/^Current time$/i)).toBeInTheDocument();
    expect(screen.queryByText(/secret/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/10:00/)).not.toBeInTheDocument();
  });

  it("defaults to running when status is missing and no result", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-2"
        argsText='{"secret":"nope"}'
        isError={false}
      />,
    );

    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("Checking the current time")).toBeVisible();
  });

  it("keeps the activity line visible in the summary when collapsed", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-3"
        status={{ type: "complete" }}
        result={{ time: "10:00" }}
        isError={false}
      />,
    );

    const details = screen.getByText("Current time").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByText("Checked the current time")).toBeVisible();
  });

  it("defaults to complete when status is missing but result exists", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-4"
        result={{ time: "10:00" }}
        isError={false}
      />,
    );

    expect(screen.getByText("complete")).toBeInTheDocument();
  });
});
