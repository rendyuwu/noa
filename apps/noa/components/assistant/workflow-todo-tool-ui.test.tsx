import { describe, expect, it } from "vitest";

import {
  coerceTodos,
  extractLatestCanonicalWorkflowTodos,
  extractLatestWorkflowTodos,
  getWorkflowTodoStatusStyle,
  isWorkflowTodoBlocked,
} from "./workflow-todo-tool-ui";

describe("workflow todo helpers", () => {
  it("coerces valid todo arrays and rejects invalid entries", () => {
    expect(
      coerceTodos([
        { content: "Step 1", status: "pending", priority: "high" },
        { content: "Step 2", status: "completed", priority: "low" },
      ]),
    ).toEqual([
      { content: "Step 1", status: "pending", priority: "high" },
      { content: "Step 2", status: "completed", priority: "low" },
    ]);

    expect(coerceTodos([{ content: 1, status: "pending", priority: "high" }])).toBeUndefined();
  });

  it("extracts canonical todos from latest message metadata", () => {
    const todos = extractLatestCanonicalWorkflowTodos([
      {
        metadata: {
          custom: {
            workflow: [{ content: "Old", status: "completed", priority: "low" }],
          },
        },
      },
      {
        metadata: {
          custom: {
            workflow: [{ content: "Latest", status: "in_progress", priority: "high" }],
          },
        },
      },
    ]);

    expect(todos).toEqual([{ content: "Latest", status: "in_progress", priority: "high" }]);
  });

  it("extracts fallback todos from update_workflow_todo tool calls", () => {
    const todos = extractLatestWorkflowTodos([
      {
        content: [
          {
            type: "tool-call",
            toolName: "update_workflow_todo",
            args: {
              todos: [{ content: "Request approval", status: "waiting_on_approval", priority: "high" }],
            },
          },
        ],
      },
    ]);

    expect(todos).toEqual([
      { content: "Request approval", status: "waiting_on_approval", priority: "high" },
    ]);
  });

  it("marks blocked statuses and returns status styling", () => {
    expect(isWorkflowTodoBlocked("waiting_on_user")).toBe(true);
    expect(isWorkflowTodoBlocked("waiting_on_approval")).toBe(true);
    expect(isWorkflowTodoBlocked("completed")).toBe(false);

    expect(getWorkflowTodoStatusStyle("waiting_on_approval")).toMatchObject({
      label: "waiting on approval",
      variant: "warning",
    });
    expect(getWorkflowTodoStatusStyle("completed")).toMatchObject({
      label: "done",
      variant: "success",
    });
    expect(getWorkflowTodoStatusStyle("cancelled")).toMatchObject({ variant: "destructive" });
  });
});
