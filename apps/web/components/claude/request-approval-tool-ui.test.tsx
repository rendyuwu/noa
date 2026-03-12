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

    expect(screen.getByText(/current time/i)).toBeInTheDocument();
    expect(screen.queryByText(/secret/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/10:00/)).not.toBeInTheDocument();
  });
});
