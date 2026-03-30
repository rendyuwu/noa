import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let mockThreadMessages: any[] = [];

vi.mock("@assistant-ui/react", () => ({
  makeAssistantToolUI: ({ render }: { render: (props: any) => unknown }) => render,
  useAssistantState: (selector: any) => selector({
    thread: {
      messages: mockThreadMessages,
    },
  }),
  useAssistantTransportSendCommand: () => vi.fn(),
}));

import {
  ClaudeToolFallback,
  ClaudeToolGroup,
  RequestApprovalToolUI,
} from "@/components/claude/request-approval-tool-ui";

describe("ClaudeToolFallback", () => {
  it("prefers evidence sections for preview and details", () => {
    mockThreadMessages = [];

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-2",
          toolName: "whm_suspend_account",
          activity: "Suspend account",
          evidenceSections: [
            {
              title: "Evidence",
              items: [
                { label: "Account", value: "alice" },
                { label: "Reason", value: "Billing" },
              ],
            },
          ],
          beforeState: [{ label: "Status", value: "Active" }],
          argumentSummary: [{ label: "Reason", value: "Should not be used" }],
        }}
      />,
    );

    expect(screen.getByText("Account: alice · Reason: Billing")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /details/i }));

    fireEvent.click(screen.getByRole("button", { name: /evidence/i }));

    expect(screen.getByText(/^Account$/i)).toBeInTheDocument();
    expect(screen.getByText(/^alice$/i)).toBeInTheDocument();
  });

  it("falls back to legacy detail sections when evidence sections are absent", () => {
    mockThreadMessages = [];

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-3",
          toolName: "whm_suspend_account",
          activity: "Suspend account",
          beforeState: [{ label: "Status", value: "Active" }],
          argumentSummary: [{ label: "Reason", value: "Billing" }],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /details/i }));

    expect(screen.getByRole("button", { name: /overview/i })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByRole("button", { name: /before state/i })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByRole("button", { name: /requested change/i })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

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

  it("shows running tools as compact live rows", () => {
    render(
      <ClaudeToolFallback
        toolName="get_current_time"
        toolCallId="tool-call-2"
        argsText='{"secret":"nope"}'
        isError={false}
      />,
    );

    expect(screen.getByText(/^Current time$/i)).toBeVisible();
    expect(screen.getByText(/^running$/i)).toBeInTheDocument();
    expect(screen.getByText(/checking the current time/i)).toBeVisible();
  });

  it("shows requires-action tools as waiting states", () => {
    render(
      <ClaudeToolFallback
        toolName="set_demo_flag"
        toolCallId="tool-call-3"
        status={{ type: "requires-action" }}
        isError={undefined}
      />,
    );

    expect(screen.getByText(/^Set Demo Flag$/i)).toBeVisible();
    expect(screen.getByText(/requires-action/i)).toBeInTheDocument();
    expect(screen.getByText(/waiting for approval before continuing set demo flag/i)).toBeVisible();
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

  it("hides resolved approval transcript entries once canonical approval state is terminal", () => {
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

    expect(screen.queryByText("Suspend cPanel account")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });
});
