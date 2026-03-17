import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let mockThreadMessages: any[] = [];

vi.mock("@assistant-ui/react", () => ({
  makeAssistantToolUI: ({ render }: { render: (props: any) => unknown }) => render,
  useAssistantState: (selector: any) => selector({
    thread: {
      messages: mockThreadMessages,
    },
  }),
}));

import {
  ClaudeToolFallback,
  ClaudeToolGroup,
  RequestApprovalToolUI,
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
    expect(screen.getByText(/^failed$/i)).toBeInTheDocument();
    expect(screen.getByText(/could not complete current time/i)).toBeVisible();
    expect(screen.queryByText(/secret/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/10:00/)).not.toBeInTheDocument();
  });

  it("hides validation-stage change tool failures that are superseded by workflow guidance", () => {
    render(
      <ClaudeToolFallback
        toolName="whm_unsuspend_account"
        toolCallId="tool-call-6"
        status={{ type: "incomplete" }}
        result={{ error_code: "invalid_tool_arguments", message: "Reason is required" }}
        isError
      />,
    );

    expect(screen.queryByText(/whm unsuspend account/i)).not.toBeInTheDocument();
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

  it("renders approval transcript entries as compact history with expandable details", () => {
    mockThreadMessages = [
      {
        metadata: {
          custom: {
            actionRequests: [
              {
                actionRequestId: "approval-1",
                toolName: "whm_suspend_account",
                risk: "CHANGE",
                arguments: {},
                status: "DENIED",
                lifecycleStatus: "denied",
              },
            ],
          },
        },
      },
    ];

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-1",
          toolName: "whm_suspend_account",
          activity: "Suspend cPanel account",
          beforeState: [{ label: "Status", value: "Active" }],
          argumentSummary: [{ label: "Reason", value: "Billing issue" }],
        }}
      />,
    );

    expect(screen.getByText("Suspend cPanel account")).toBeInTheDocument();
    expect(screen.getByText("History snapshot")).toBeInTheDocument();
    expect(screen.getByText(/^denied$/i)).toBeInTheDocument();
    expect(screen.getByText("View request details")).toBeInTheDocument();
    expect(screen.queryByText(/canonical approval panel/i)).not.toBeInTheDocument();
  });
});
