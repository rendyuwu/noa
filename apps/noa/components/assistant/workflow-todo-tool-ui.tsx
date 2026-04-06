"use client";

import { Check, Circle, Clock3, PauseCircle, XCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { makeAssistantToolUI } from "@assistant-ui/react";

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

type BadgeVariant = "default" | "secondary" | "destructive" | "success" | "warning" | "info" | "muted" | "outline";

type StatusStyle = {
  label: string;
  variant: BadgeVariant;
  Icon: LucideIcon;
};

const STATUS_STYLES: Record<WorkflowTodoStatus, StatusStyle> = {
  pending: {
    label: "pending",
    variant: "muted",
    Icon: Circle,
  },
  in_progress: {
    label: "in progress",
    variant: "info",
    Icon: Clock3,
  },
  waiting_on_user: {
    label: "waiting on user",
    variant: "warning",
    Icon: PauseCircle,
  },
  waiting_on_approval: {
    label: "waiting on approval",
    variant: "warning",
    Icon: PauseCircle,
  },
  completed: {
    label: "done",
    variant: "success",
    Icon: Check,
  },
  cancelled: {
    label: "cancelled",
    variant: "destructive",
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

export const WorkflowTodoToolUI = makeAssistantToolUI({
  toolName: "update_workflow_todo",
  render: () => null,
});
