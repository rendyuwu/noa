import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ClaudeToolFallback,
  ClaudeToolGroup,
} from "@/components/claude/request-approval-tool-ui";

describe("ClaudeToolFallback", () => {
  it("shows successful tools immediately after completion", () => {
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

    expect(screen.getByText(/^Current time$/i)).toBeVisible();
    expect(screen.getByText("complete")).toBeInTheDocument();
  });

  it("lingers for 1s then fades out before unmounting", () => {
    vi.useFakeTimers();

    try {
      render(
        <ClaudeToolFallback
          toolName="get_current_time"
          toolCallId="tool-call-fadeout"
          status={{ type: "complete" }}
          result={{ time: "10:00" }}
          isError={false}
        />,
      );

      expect(screen.getByText(/^Current time$/i)).toBeVisible();

      act(() => {
        vi.advanceTimersByTime(1000);
      });
      const row = screen.getByText(/^Current time$/i).closest("div.overflow-hidden");
      expect(row).toHaveClass("opacity-0", "max-h-0");

      act(() => {
        vi.advanceTimersByTime(200);
      });
      expect(screen.queryByText(/^Current time$/i)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows one-line running activity when status is missing", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-2"
        argsText='{"secret":"nope"}'
        isError={false}
      />,
    );

    expect(screen.getByText(/^Current time$/i)).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText(/checking the current time/i)).toBeVisible();
    expect(screen.queryByText(/secret/i)).not.toBeInTheDocument();
  });

  it("shows one-line requires-action activity", () => {
    render(
      <ClaudeToolFallback
        toolName="set_demo_flag"
        toolCallId="tool-call-3"
        status={{ type: "requires-action" }}
        isError={undefined}
      />,
    );

    expect(screen.getByText("requires-action")).toBeInTheDocument();
    expect(screen.getByText(/waiting for approval/i)).toBeVisible();
  });

  it("shows incomplete activity row on error", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-4"
        status={{ type: "incomplete" }}
        isError
      />,
    );

    expect(screen.getByText("incomplete")).toBeInTheDocument();
    expect(screen.getByText(/could not complete current time/i)).toBeVisible();
  });

  it("unmounts wrapper after successful tool fadeout", () => {
    vi.useFakeTimers();

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

    expect(screen.getByText(/^Current time$/i)).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1200);
    });

    expect(container.firstChild).toBeNull();
    vi.useRealTimers();
  });
});
