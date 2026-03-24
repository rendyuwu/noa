import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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

import { WorkflowTodoCard, WorkflowTodoToolUI } from "@/components/claude/workflow-todo-tool-ui";

describe("WorkflowTodoCard", () => {
  it("includes evidence sections from tool payload in workflow details", () => {
    mockThreadMessages = [];

    render(
      <WorkflowTodoToolUI
        args={{
          todos: [
            { content: "Preflight", status: "completed", priority: "high" },
            { content: "Execute change", status: "completed", priority: "high" },
          ],
          evidenceSections: [
            {
              title: "Execution evidence",
              items: [{ label: "Server", value: "cp01" }],
            },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /details/i }));

    fireEvent.click(screen.getByRole("button", { name: /execution evidence/i }));

    expect(screen.getByText("Execution evidence")).toBeVisible();
    expect(screen.getByText("Server")).toBeVisible();
    expect(screen.getByText("cp01")).toBeVisible();
  });

  it("falls back to canonical metadata evidence sections when payload has none", () => {
    mockThreadMessages = [
      {
        metadata: {
          custom: {
            evidenceSections: [
              {
                title: "Canonical evidence",
                items: [{ label: "Ticket", value: "INC-42" }],
              },
            ],
          },
        },
      },
    ];

    render(
      <WorkflowTodoToolUI
        args={{
          todos: [
            { content: "Preflight", status: "completed", priority: "high" },
            { content: "Execute change", status: "completed", priority: "high" },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /details/i }));

    fireEvent.click(screen.getByRole("button", { name: /canonical evidence/i }));

    expect(screen.getByText("Canonical evidence")).toBeVisible();
    expect(screen.getByText("Ticket")).toBeVisible();
    expect(screen.getByText("INC-42")).toBeVisible();
  });

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
    expect(screen.getByText(/2\/2 steps/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /details/i }));

    expect(screen.getByText("Completed")).toBeVisible();
  });

  it("prefers canonical workflow todos when tool payload is terminal-only", () => {
    mockThreadMessages = [
      {
        metadata: {
          custom: {
            workflow: [
              { content: "Preflight check", status: "completed", priority: "high" },
              { content: "Reason captured", status: "completed", priority: "high" },
              { content: "Request approval", status: "completed", priority: "high" },
              { content: "Execute change", status: "completed", priority: "high" },
              { content: "Postflight verification", status: "completed", priority: "high" },
            ],
          },
        },
      },
    ];

    render(
      <WorkflowTodoToolUI
        args={{
          todos: [{ content: "Execute change", status: "completed", priority: "high" }],
        }}
      />,
    );

    expect(screen.getByText("Run summary")).toBeVisible();
    expect(screen.getByText(/5\/5 steps/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /details/i }));

    expect(screen.getByText("Preflight check")).toBeVisible();
    expect(screen.getByText("Reason captured")).toBeVisible();
    expect(screen.getByText("Request approval")).toBeVisible();
    expect(screen.getByText("Execute change")).toBeVisible();
    expect(screen.getByText("Postflight verification")).toBeVisible();
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
