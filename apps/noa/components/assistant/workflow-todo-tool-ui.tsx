"use client";

import { Check, Circle, Clock3, PauseCircle, XCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { makeAssistantToolUI, useAssistantState } from "@assistant-ui/react";

export type WorkflowTodoStatus =
  | "pending"
  | "in_progress"
  | "waiting_on_user"
  | "waiting_on_approval"
  | "completed"
  | "cancelled";

export type WorkflowTodoPriority = "high" | "medium" | "low";

export type WorkflowTodoItem = {
  content: string;
  status: WorkflowTodoStatus;
  priority: WorkflowTodoPriority;
};

export const BLOCKED_WORKFLOW_TODO_STATUSES = ["waiting_on_user", "waiting_on_approval"] as const;

type StatusStyle = {
  label: string;
  className: string;
  Icon: LucideIcon;
};

const STATUS_STYLES: Record<WorkflowTodoStatus, StatusStyle> = {
  pending: {
    label: "pending",
    className: "bg-surface-2 text-muted",
    Icon: Circle,
  },
  in_progress: {
    label: "in progress",
    className: "bg-accent/15 text-accent",
    Icon: Clock3,
  },
  waiting_on_user: {
    label: "waiting on user",
    className: "bg-amber-100 text-amber-900",
    Icon: PauseCircle,
  },
  waiting_on_approval: {
    label: "waiting on approval",
    className: "bg-sky-100 text-sky-900",
    Icon: PauseCircle,
  },
  completed: {
    label: "done",
    className: "bg-surface-2 text-muted",
    Icon: Check,
  },
  cancelled: {
    label: "cancelled",
    className: "bg-red-100 text-red-900",
    Icon: XCircle,
  },
};

function isTodoItem(value: unknown): value is WorkflowTodoItem {
  if (!value || typeof value !== "object") {
    return false;
  }

  const record = value as Record<string, unknown>;

  return (
    typeof record.content === "string" &&
    (record.status === "pending" ||
      record.status === "in_progress" ||
      record.status === "waiting_on_user" ||
      record.status === "waiting_on_approval" ||
      record.status === "completed" ||
      record.status === "cancelled") &&
    (record.priority === "high" || record.priority === "medium" || record.priority === "low")
  );
}

function coerceRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

export function coerceTodos(value: unknown): WorkflowTodoItem[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }

  if (value.length === 0) {
    return [];
  }

  const todos = value.filter(isTodoItem);
  return todos.length ? todos : undefined;
}

export function getWorkflowTodoStatusStyle(status: WorkflowTodoStatus): StatusStyle {
  return STATUS_STYLES[status] ?? STATUS_STYLES.pending;
}

export function isWorkflowTodoBlocked(status: WorkflowTodoStatus): boolean {
  return BLOCKED_WORKFLOW_TODO_STATUSES.includes(
    status as (typeof BLOCKED_WORKFLOW_TODO_STATUSES)[number],
  );
}

export function extractLatestCanonicalWorkflowTodos(messages: unknown): WorkflowTodoItem[] | undefined {
  if (!Array.isArray(messages)) {
    return undefined;
  }

  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = coerceRecord(messages[messageIndex]);
    const metadata = coerceRecord(message?.metadata);
    const custom = coerceRecord(metadata?.custom);

    if (!custom || !("workflow" in custom)) {
      continue;
    }

    return coerceTodos(custom.workflow);
  }

  return undefined;
}

export function extractLatestWorkflowTodos(messages: unknown): WorkflowTodoItem[] {
  if (!Array.isArray(messages)) {
    return [];
  }

  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = messages[messageIndex] as Record<string, unknown> | undefined;
    const content = Array.isArray(message?.content) ? message.content : [];

    for (let contentIndex = content.length - 1; contentIndex >= 0; contentIndex -= 1) {
      const part = content[contentIndex] as Record<string, unknown> | undefined;
      if (part?.type !== "tool-call" || part.toolName !== "update_workflow_todo") {
        continue;
      }

      const argsTodos =
        part.args && typeof part.args === "object"
          ? coerceTodos((part.args as Record<string, unknown>).todos)
          : undefined;
      const resultTodos =
        part.result && typeof part.result === "object"
          ? coerceTodos((part.result as Record<string, unknown>).todos)
          : undefined;

      return argsTodos ?? resultTodos ?? [];
    }
  }

  return [];
}

function WorkflowTodoInline({ todos }: { todos: WorkflowTodoItem[] }) {
  const completedCount = todos.filter((todo) => todo.status === "completed").length;

  return (
    <div className="mt-3 rounded-xl border border-border bg-bg/20 px-3 py-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium text-text">Workflow progress</div>
        <div className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted">
          {completedCount}/{todos.length}
        </div>
      </div>
      <ul className="mt-3 space-y-2">
        {todos.map((todo, index) => {
          const style = getWorkflowTodoStatusStyle(todo.status);
          return (
            <li key={`${todo.content}-${index}`} className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-sm text-text">{todo.content}</div>
                <div className="mt-0.5 text-[11px] uppercase tracking-[0.08em] text-muted">{todo.priority}</div>
              </div>
              <span
                className={[
                  "inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px]",
                  style.className,
                ].join(" ")}
              >
                <style.Icon className="size-3" />
                <span>{style.label}</span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export const WorkflowTodoToolUI = makeAssistantToolUI({
  toolName: "update_workflow_todo",
  render: ({ args, result }: { args: Record<string, unknown>; result?: unknown }) => {
    const threadMessages = useAssistantState((state) => state.thread?.messages) as unknown[] | undefined;

    const canonicalTodos = extractLatestCanonicalWorkflowTodos(threadMessages);
    const argsTodos = coerceTodos(args.todos);
    const resultTodos =
      result && typeof result === "object"
        ? coerceTodos((result as Record<string, unknown>).todos)
        : undefined;

    const todos = canonicalTodos ?? argsTodos ?? resultTodos ?? [];

    const hasBlocked = todos.some((todo) => isWorkflowTodoBlocked(todo.status));

    if (!todos.length) {
      return null;
    }

    return (
      <div data-testid="workflow-todo-tool-ui">
        {hasBlocked ? (
          <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            Workflow is waiting on external input.
          </div>
        ) : null}
        <WorkflowTodoInline todos={todos} />
      </div>
    );
  },
});
