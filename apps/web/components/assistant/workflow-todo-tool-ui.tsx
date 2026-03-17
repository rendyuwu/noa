"use client";

import { makeAssistantToolUI, useAssistantState } from "@assistant-ui/react";
import { CheckIcon, ChevronRightIcon, Cross2Icon, DotFilledIcon } from "@radix-ui/react-icons";

import { extractLatestCanonicalActionRequests } from "@/components/assistant/approval-state";
import {
  toggleAssistantDetailSheet,
  useAssistantDetailSheet,
} from "@/components/assistant/assistant-detail-sheet-store";

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
  waiting_on_user: {
    label: "waiting on user",
    className: "bg-amber-100 text-amber-900",
    Icon: DotFilledIcon,
  },
  waiting_on_approval: {
    label: "waiting on approval",
    className: "bg-sky-100 text-sky-900",
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
      record.status === "waiting_on_user" ||
      record.status === "waiting_on_approval" ||
      record.status === "completed" ||
      record.status === "cancelled") &&
    (record.priority === "high" ||
      record.priority === "medium" ||
      record.priority === "low")
  );
}

function coerceRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

export function coerceTodos(value: unknown): WorkflowTodoItem[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const todos = value.filter(isTodoItem);
  return todos.length ? todos : [];
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
  if (!Array.isArray(messages)) return undefined;

  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = coerceRecord(messages[messageIndex]);
    const metadata = coerceRecord(message?.metadata);
    const custom = coerceRecord(metadata?.custom);
    if (!custom || !("workflow" in custom)) continue;
    return coerceTodos(custom.workflow);
  }

  return undefined;
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
  const threadMessages = useAssistantState(({ thread }: any) => thread?.messages);
  const hasApprovalHistory = (extractLatestCanonicalActionRequests(threadMessages) ?? []).length > 0;
  const detailSheet = useAssistantDetailSheet();
  const detailKey = `workflow:${todos.map((todo) => `${todo.content}:${todo.status}`).join("|")}`;
  const completedCount = todos.filter((todo) => todo.status === "completed").length;
  const cancelledCount = todos.filter((todo) => todo.status === "cancelled").length;
  const isTerminal = todos.every(
    (todo) => todo.status === "completed" || todo.status === "cancelled",
  );

  if (!todos.length || !isTerminal || hasApprovalHistory) {
    return null;
  }

  const summaryParts = [
    cancelledCount > 0 ? "Run ended" : "Completed",
    `${completedCount}/${todos.length} steps`,
  ].filter(Boolean);

  return (
    <div className="mt-3 rounded-lg border border-border/60 bg-bg/10 px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm text-text">Run details</div>
          <div className="mt-1 text-xs text-muted">{summaryParts.join(" · ")}</div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="rounded-full bg-emerald-100/80 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-emerald-900">
            {cancelledCount > 0 ? "ended" : "done"}
          </div>
          <button
            type="button"
            onClick={() => {
              toggleAssistantDetailSheet({
                open: true,
                key: detailKey,
                kind: "workflow",
                title: "Run details",
                subtitle: summaryParts.join(" · "),
                badge: cancelledCount > 0 ? "ended" : "done",
                badgeClassName:
                  cancelledCount > 0
                    ? "bg-slate-200/80 text-slate-800"
                    : "bg-emerald-100/80 text-emerald-900",
                todos,
              });
            }}
            className="inline-flex items-center gap-1 text-xs font-medium text-muted transition hover:text-text"
          >
            {detailSheet.open && detailSheet.key === detailKey ? "Hide details" : "Details"}
            <ChevronRightIcon width={14} height={14} />
          </button>
        </div>
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
