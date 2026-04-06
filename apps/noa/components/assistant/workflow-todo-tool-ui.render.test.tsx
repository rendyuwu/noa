import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@assistant-ui/react", () => ({
  makeAssistantToolUI:
    ({ render }: { render: (props: Record<string, unknown>) => ReactNode }) =>
    (props: Record<string, unknown>) =>
      render(props),
}));

import { WorkflowTodoToolUI } from "./workflow-todo-tool-ui";

const WorkflowTodoToolUIAny = WorkflowTodoToolUI as unknown as (props: { args: { todos: never[] } }) => ReactNode;

describe("WorkflowTodoToolUI", () => {
  it("does not render an inline workflow card", () => {
    const { container } = render(<WorkflowTodoToolUIAny args={{ todos: [] }} />);

    expect(container).toBeEmptyDOMElement();
  });
});
