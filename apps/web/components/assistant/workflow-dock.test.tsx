import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkflowDock } from "./workflow-dock";

describe("WorkflowDock", () => {
  it("prioritizes in-progress steps as the active step", () => {
    render(
      <WorkflowDock
        isRunning
        todos={[
          { content: "Preflight", status: "completed", priority: "high" },
          { content: "Apply change", status: "in_progress", priority: "high" },
          { content: "Confirm result", status: "pending", priority: "medium" },
        ]}
      />,
    );

    expect(screen.getByTestId("workflow-active-step")).toHaveTextContent("Apply change");
  });

  it("surfaces blocked statuses distinctly when execution is paused", () => {
    render(
      <WorkflowDock
        isRunning={false}
        todos={[
          { content: "Request approval", status: "waiting_on_approval", priority: "high" },
          { content: "Apply change", status: "pending", priority: "high" },
        ]}
      />,
    );

    expect(screen.getByText("Workflow paused")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-active-step")).toHaveTextContent("Request approval");
    expect(screen.getByText(/waiting on approval/i)).toBeInTheDocument();
  });

  it("hides stale incomplete workflows after a run is no longer live", () => {
    vi.useFakeTimers();

    const { rerender } = render(
      <WorkflowDock
        isRunning
        todos={[{ content: "Apply change", status: "in_progress", priority: "high" }]}
      />,
    );

    rerender(
      <WorkflowDock
        isRunning={false}
        todos={[{ content: "Apply change", status: "pending", priority: "high" }]}
      />,
    );

    expect(screen.queryByTestId("workflow-todo-dock")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
