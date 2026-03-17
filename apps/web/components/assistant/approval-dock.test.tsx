import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApprovalDock } from "./approval-dock";

const sendCommand = vi.fn();

vi.mock("@assistant-ui/react", () => ({
  useAssistantTransportSendCommand: () => sendCommand,
}));

describe("ApprovalDock", () => {
  it("clears optimistic deny state when the request becomes denied", () => {
    const { rerender } = render(
      <ApprovalDock
        requests={[
          {
            actionRequestId: "approval-1",
            toolName: "whm_suspend_account",
            risk: "CHANGE",
            arguments: { server_ref: "web2", username: "rendy", reason: "smoke deny" },
            status: "PENDING",
            lifecycleStatus: "requested",
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Deny" }));

    expect(screen.getByText("Sending denial")).toBeInTheDocument();
    expect(sendCommand).toHaveBeenCalledWith({ type: "deny-action", actionRequestId: "approval-1" });

    rerender(
      <ApprovalDock
        requests={[
          {
            actionRequestId: "approval-1",
            toolName: "whm_suspend_account",
            risk: "CHANGE",
            arguments: { server_ref: "web2", username: "rendy", reason: "smoke deny" },
            status: "DENIED",
            lifecycleStatus: "denied",
          },
        ]}
      />,
    );

    expect(screen.queryByText("Action request denied")).not.toBeInTheDocument();
    expect(screen.queryByText("Sending denial")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deny" })).not.toBeInTheDocument();
  });

  it("clears optimistic approval state when the request finishes", () => {
    const { rerender } = render(
      <ApprovalDock
        requests={[
          {
            actionRequestId: "approval-2",
            toolName: "whm_unsuspend_account",
            risk: "CHANGE",
            arguments: { server_ref: "web2", username: "rendy", reason: "smoke restore" },
            status: "PENDING",
            lifecycleStatus: "requested",
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(screen.getByText("Sending approval")).toBeInTheDocument();
    expect(sendCommand).toHaveBeenCalledWith({ type: "approve-action", actionRequestId: "approval-2" });

    rerender(
      <ApprovalDock
        requests={[
          {
            actionRequestId: "approval-2",
            toolName: "whm_unsuspend_account",
            risk: "CHANGE",
            arguments: { server_ref: "web2", username: "rendy", reason: "smoke restore" },
            status: "APPROVED",
            lifecycleStatus: "finished",
          },
        ]}
      />,
    );

    expect(screen.queryByText("Approved action finished")).not.toBeInTheDocument();
    expect(screen.queryByText("Sending approval")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("hides denied requests once nothing live remains", () => {
    render(
      <ApprovalDock
        requests={[
          {
            actionRequestId: "approval-3",
            toolName: "whm_suspend_account",
            risk: "CHANGE",
            arguments: { server_ref: "web2", username: "rendy", reason: "smoke deny" },
            status: "DENIED",
            lifecycleStatus: "denied",
          },
        ]}
      />,
    );

    expect(screen.queryByTestId("approval-dock-card")).not.toBeInTheDocument();
  });
});
