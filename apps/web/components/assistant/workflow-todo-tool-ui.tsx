"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import { CheckIcon, Cross2Icon, DotFilledIcon } from "@radix-ui/react-icons";

type WorkflowTodoStatus = "pending" | "in_progress" | "completed" | "cancelled";
type WorkflowTodoPriority = "high" | "medium" | "low";

export type WorkflowTodoItem = {
  content: string;
  status: WorkflowTodoStatus;
  priority: WorkflowTodoPriority;
};

type StatusStyle = {
  label: string;
  className: string;
  Icon: typeof DotFilledIcon;
};

const STATUS_STYLES: Record<WorkflowTodoStatus, StatusStyle> = {
  pending: {
    label: "pending",
    className: "bg-surface-2 text-muted",
    Icon: DotFilledIcon,
  },
  in_progress: {
    label: "in progress",
    className: "bg-accent/15 text-accent",
    Icon: DotFilledIcon,
  },
  completed: {
    label: "done",
    className: "bg-emerald-50 text-emerald-800",
    Icon: CheckIcon,
  },
  cancelled: {
    label: "cancelled",
    className: "bg-red-50 text-red-800",
    Icon: Cross2Icon,
  },
};

function isTodoItem(value: unknown): value is WorkflowTodoItem {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.content === "string" &&
    (record.status === "pending" ||
      record.status === "in_progress" ||
      record.status === "completed" ||
      record.status === "cancelled") &&
    (record.priority === "high" ||
      record.priority === "medium" ||
      record.priority === "low")
  );
}

export function coerceTodos(value: unknown): WorkflowTodoItem[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const todos = value.filter(isTodoItem);
  return todos.length ? todos : [];
}

export function extractLatestWorkflowTodos(messages: unknown): WorkflowTodoItem[] {
  if (!Array.isArray(messages)) return [];

  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = messages[messageIndex] as Record<string, unknown> | undefined;
    const content = Array.isArray(message?.content) ? message.content : [];
    for (let contentIndex = content.length - 1; contentIndex >= 0; contentIndex -= 1) {
      const part = content[contentIndex] as Record<string, unknown> | undefined;
      if (part?.type !== "tool-call" || part.toolName !== "update_workflow_todo") continue;
      const argsTodos = coerceTodos(part.args && typeof part.args === "object" ? (part.args as Record<string, unknown>).todos : undefined);
      const resultTodos =
        part.result && typeof part.result === "object"
          ? coerceTodos((part.result as Record<string, unknown>).todos)
          : undefined;
      return argsTodos ?? resultTodos ?? [];
    }
  }

  return [];
}

export function WorkflowTodoCard({ todos }: { todos: WorkflowTodoItem[] }) {
  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.035),0_0_0_0.5px_rgba(0,0,0,0.08)]">
      <div className="flex items-start justify-between gap-3 border-b border-border bg-surface-2 px-4 py-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-text">Workflow</div>
          <div className="mt-0.5 text-xs text-muted">
            {todos.length === 1 ? "1 step" : `${todos.length} steps`}
          </div>
        </div>
      </div>

      <div className="px-4 py-3">
        {todos.length ? (
          <ul className="space-y-2">
            {todos.map((todo, index) => {
              const style = STATUS_STYLES[todo.status] ?? STATUS_STYLES.pending;
              const Icon = style.Icon;
              return (
                <li
                  key={`${todo.content}-${index}`}
                  className="flex items-start justify-between gap-3 rounded-lg bg-bg/40 px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="text-sm text-text">{todo.content}</div>
                  </div>
                  <div
                    className={[
                      "shrink-0 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px]",
                      style.className,
                    ].join(" ")}
                  >
                    <Icon width={12} height={12} />
                    <span className="leading-none">{style.label}</span>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="rounded-lg border border-border bg-bg/40 px-3 py-2 text-sm text-muted">
            No workflow steps.
          </div>
        )}
      </div>
    </div>
  );
}

export const WorkflowTodoToolUI = makeAssistantToolUI({
  toolName: "update_workflow_todo",
  render: ({ args, result }: { args: Record<string, unknown>; result?: unknown }) => {
    const argsTodos = coerceTodos(args.todos);
    const resultTodos =
      result && typeof result === "object" && result !== null
        ? coerceTodos((result as Record<string, unknown>).todos)
        : undefined;
    const todos = argsTodos ?? resultTodos ?? [];
    return <WorkflowTodoCard todos={todos} />;
  },
});
