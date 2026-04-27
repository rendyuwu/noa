import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let mockThreadMessages: any[] = [];
const mockSendCommand = vi.fn();

vi.mock("@assistant-ui/react", () => ({
  makeAssistantToolUI: ({ render }: { render: (props: any) => unknown }) => render,
  useAssistantState: (selector: any) => selector({
    thread: {
      messages: mockThreadMessages,
    },
  }),
  useAssistantTransportSendCommand: () => mockSendCommand,
}));

import {
  ClaudeToolFallback,
  ClaudeToolGroup,
  RequestApprovalToolUI,
} from "@/components/claude/request-approval-tool-ui";

describe("ClaudeToolFallback", () => {
  it("prefers evidence sections for preview and details", () => {
    mockThreadMessages = [];

    const { container } = render(
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

    const card = container.firstElementChild as HTMLElement;
    expect(card).toHaveClass("rounded-2xl");
    expect(card).toHaveClass("bg-card/80");

    expect(screen.getByText("Account: alice · Reason: Billing")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /details/i }));

    fireEvent.click(screen.getByRole("button", { name: /evidence/i }));

    expect(screen.getByText(/^Account$/i)).toBeInTheDocument();
    expect(screen.getByText(/^alice$/i)).toBeInTheDocument();
  });

  it("does not duplicate legacy fallback sections when canonical evidence sections exist", () => {
    mockThreadMessages = [];

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-2b",
          toolName: "whm_suspend_account",
          activity: "Suspend account",
          evidenceSections: [
            {
              title: "Before state",
              items: [{ label: "Status", value: "Active" }],
            },
            {
              title: "Requested change",
              items: [{ label: "Reason", value: "Billing" }],
            },
          ],
          beforeState: [{ label: "Status", value: "Legacy Active" }],
          argumentSummary: [{ label: "Reason", value: "Legacy Billing" }],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /details/i }));

    expect(screen.getAllByText(/^Before state$/i)).toHaveLength(1);
    expect(screen.getAllByText(/^Requested change$/i)).toHaveLength(1);
    expect(screen.getByText(/^Active$/i)).toBeInTheDocument();
    expect(screen.getByText(/^Billing$/i)).toBeInTheDocument();
    expect(screen.queryByText(/Legacy Active/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Legacy Billing/i)).not.toBeInTheDocument();
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
        toolName="mock_change_tool"
        toolCallId="tool-call-3"
        status={{ type: "requires-action" }}
        isError={undefined}
      />,
    );

    expect(screen.getByText(/^Mock Change Tool$/i)).toBeVisible();
    expect(screen.getByText(/requires-action/i)).toBeInTheDocument();
    expect(screen.getByText(/waiting for approval before continuing mock change tool/i)).toBeVisible();
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

  // V72: Confirmation dialog tests
  it("clicking Approve opens confirmation dialog instead of dispatching (V72)", () => {
    mockThreadMessages = [];
    mockSendCommand.mockClear();

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-v72-1",
          toolName: "whm_suspend_account",
          activity: "Suspend account",
          argumentSummary: [
            { label: "Subject", value: "alice" },
            { label: "Reason", value: "Billing overdue" },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));

    // Dialog must open with title and confirm button
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: /confirm approve/i })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /confirm approve/i })).toBeInTheDocument();
    // sendCommand must NOT have been called yet
    expect(mockSendCommand).not.toHaveBeenCalled();
  });

  it("clicking Deny opens confirmation dialog instead of dispatching (V72)", () => {
    mockThreadMessages = [];
    mockSendCommand.mockClear();

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-v72-2",
          toolName: "whm_suspend_account",
          activity: "Suspend account",
          argumentSummary: [
            { label: "Subject", value: "alice" },
            { label: "Reason", value: "Billing overdue" },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^deny$/i }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: /confirm deny/i })).toBeInTheDocument();
    expect(mockSendCommand).not.toHaveBeenCalled();
  });

  it("confirmation dialog shows activity, subject, and reason (V72)", () => {
    mockThreadMessages = [];
    mockSendCommand.mockClear();

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-v72-3",
          toolName: "whm_suspend_account",
          activity: "Suspend account",
          argumentSummary: [
            { label: "Subject", value: "alice" },
            { label: "Reason", value: "Billing overdue" },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));

    const dialog = screen.getByRole("dialog");
    // Activity shown as both dialog description and in summary list
    const activityDd = within(dialog).getAllByText("Suspend account");
    expect(activityDd.length).toBeGreaterThanOrEqual(1);
    // Subject and Reason in key-value list
    expect(within(dialog).getByText("Subject:")).toBeInTheDocument();
    expect(within(dialog).getByText("alice")).toBeInTheDocument();
    expect(within(dialog).getByText("Reason:")).toBeInTheDocument();
    expect(within(dialog).getByText("Billing overdue")).toBeInTheDocument();
  });

  it("confirming in dialog dispatches approve command (V72)", () => {
    mockThreadMessages = [];
    mockSendCommand.mockClear();

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-v72-4",
          toolName: "whm_suspend_account",
          activity: "Suspend account",
          argumentSummary: [
            { label: "Subject", value: "alice" },
            { label: "Reason", value: "Billing overdue" },
          ],
        }}
      />,
    );

    // Open dialog
    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(mockSendCommand).not.toHaveBeenCalled();

    // Confirm in dialog
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /confirm approve/i }));

    expect(mockSendCommand).toHaveBeenCalledWith({
      type: "approve-action",
      actionRequestId: "approval-v72-4",
    });
  });

  it("confirming in dialog dispatches deny command (V72)", () => {
    mockThreadMessages = [];
    mockSendCommand.mockClear();

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-v72-5",
          toolName: "whm_suspend_account",
          activity: "Suspend account",
          argumentSummary: [
            { label: "Subject", value: "alice" },
            { label: "Reason", value: "Billing overdue" },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^deny$/i }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /confirm deny/i }));

    expect(mockSendCommand).toHaveBeenCalledWith({
      type: "deny-action",
      actionRequestId: "approval-v72-5",
    });
  });

  it("canceling dialog does not dispatch any command (V72)", () => {
    mockThreadMessages = [];
    mockSendCommand.mockClear();

    render(
      <RequestApprovalToolUI
        args={{
          actionRequestId: "approval-v72-6",
          toolName: "whm_suspend_account",
          activity: "Suspend account",
          argumentSummary: [
            { label: "Subject", value: "alice" },
            { label: "Reason", value: "Billing overdue" },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

    expect(mockSendCommand).not.toHaveBeenCalled();
  });
});
