import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockToggleAssistantDetailSheet } = vi.hoisted(() => ({
  mockToggleAssistantDetailSheet: vi.fn(),
}));

let mockThreadMessages: any[] = [];

vi.mock("@assistant-ui/react", () => ({
  makeAssistantToolUI: ({ render }: { render: (props: any) => unknown }) => render,
  useAssistantState: (selector: any) =>
    selector({
      thread: {
        messages: mockThreadMessages,
      },
    }),
}));

vi.mock("@/components/assistant/assistant-detail-sheet-store", () => ({
  toggleAssistantDetailSheet: mockToggleAssistantDetailSheet,
  useAssistantDetailSheet: () => ({ open: false, key: null }),
}));

import { WorkflowTodoCard } from "@/components/claude/workflow-todo-tool-ui";

describe("WorkflowTodoCard", () => {
  it("renders terminal workflow runs as compact details with expandable summary", () => {
    mockThreadMessages = [];

    render(
      <WorkflowTodoCard
        todos={[
          { content: "Preflight", status: "completed", priority: "high" },
          { content: "Execute change", status: "completed", priority: "high" },
        ]}
      />,
    );

    expect(screen.getByText("Run summary")).toBeVisible();
    expect(screen.getByText(/completed .* 2\/2 steps/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /details/i }));

    expect(mockToggleAssistantDetailSheet).toHaveBeenCalledWith(
      expect.objectContaining({
        open: true,
        kind: "workflow",
        title: "Run summary",
      }),
    );
  });

  it("keeps the terminal workflow receipt visible when approval history exists", () => {
    mockThreadMessages = [
      {
        metadata: {
          custom: {
            actionRequests: [
              {
                actionRequestId: "approval-1",
                toolName: "whm_unsuspend_account",
                risk: "CHANGE",
                arguments: {},
                status: "FINISHED",
                lifecycleStatus: "finished",
              },
            ],
          },
        },
      },
    ];

    render(
      <WorkflowTodoCard
        todos={[
          { content: "Preflight", status: "completed", priority: "high" },
          { content: "Apply change", status: "completed", priority: "high" },
        ]}
      />,
    );

    expect(screen.getByText("Run summary")).toBeVisible();
  });
});
