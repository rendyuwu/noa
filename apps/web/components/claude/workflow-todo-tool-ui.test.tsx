import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkflowTodoCard } from "@/components/claude/workflow-todo-tool-ui";

describe("WorkflowTodoCard", () => {
  it("renders each todo content with a status label", () => {
    render(
      <WorkflowTodoCard
        todos={[
          { content: "Preflight", status: "in_progress", priority: "high" },
          { content: "Request approval", status: "pending", priority: "high" },
        ]}
      />,
    );

    expect(screen.getByText("Workflow snapshot recorded")).toBeVisible();
    expect(screen.getByText(/2 steps captured in this update/i)).toBeInTheDocument();
    expect(screen.getByText(/1 active/i)).toBeInTheDocument();
    expect(screen.getByText("View captured steps")).toBeInTheDocument();
    expect(screen.getByText("Preflight")).toBeInTheDocument();
    expect(screen.getByText("Request approval")).toBeInTheDocument();
  });
});
