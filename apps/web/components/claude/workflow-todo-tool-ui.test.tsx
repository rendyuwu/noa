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

    expect(screen.getByText("Preflight")).toBeVisible();
    expect(screen.getByText("Request approval")).toBeVisible();

    expect(screen.getByText(/in progress/i)).toBeInTheDocument();
    expect(screen.getByText(/pending/i)).toBeInTheDocument();
  });
});
