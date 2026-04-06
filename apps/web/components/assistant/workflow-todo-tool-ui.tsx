"use client";

import { useId, useState } from "react";

import { makeAssistantToolUI, useAssistantState } from "@assistant-ui/react";
import { CheckIcon, ChevronRightIcon, Cross2Icon, DotFilledIcon } from "@radix-ui/react-icons";

import type { AssistantDetailEvidenceSection } from "@/components/assistant/approval-state";
import {
  coerceDetailEvidenceSections,
  extractLatestCanonicalEvidenceSections,
} from "@/components/assistant/approval-state";
import { DetailSections } from "@/components/assistant/detail-sections";
import {
  DisclosureSection,
  TruncatedItemList,
  TruncatedText,
} from "@/components/assistant/inline-disclosure";

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
    className: "bg-surface-2 text-muted",
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
  if (value.length === 0) return [];
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

function normalizeTodoContent(content: string): string {
  return content.trim().toLowerCase();
}

function todosHaveOverlappingContent(a: WorkflowTodoItem[], b: WorkflowTodoItem[]): boolean {
  const aContents = a.map((todo) => normalizeTodoContent(todo.content)).filter(Boolean);
  const bContents = b.map((todo) => normalizeTodoContent(todo.content)).filter(Boolean);

  for (const aContent of aContents) {
    if (aContent.length < 8) continue;
    for (const bContent of bContents) {
      if (bContent.length < 8) continue;
      if (aContent.includes(bContent) || bContent.includes(aContent)) return true;
    }
  }

  return false;
}

export function WorkflowTodoCard({
  todos,
  renderGateTodos,
  evidenceSections,
}: {
  todos: WorkflowTodoItem[];
  renderGateTodos?: WorkflowTodoItem[];
  evidenceSections?: AssistantDetailEvidenceSection[];
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const baseId = useId();
  const toggleId = `${baseId}-workflow-details-toggle`;
  const panelId = `${baseId}-workflow-details-panel`;
  const completedCount = todos.filter((todo) => todo.status === "completed").length;
  const cancelledCount = todos.filter((todo) => todo.status === "cancelled").length;
  const gateTodos = renderGateTodos ?? todos;
  const isTerminal = todos.every(
    (todo) => todo.status === "completed" || todo.status === "cancelled",
  );

  if (!gateTodos.length || !todos.length || !isTerminal) {
    return null;
  }

  const summaryParts = [
    `${completedCount}/${todos.length} steps`,
    cancelledCount > 0 ? `${cancelledCount} cancelled` : null,
  ].filter(Boolean);
  const title = "Run summary";
  const badge = cancelledCount > 0 ? "ended" : "done";
  const badgeClassName = "bg-surface-2 text-muted";
  const sections = evidenceSections ?? [];

  return (
    <div className="mt-3 rounded-lg border border-border/60 bg-bg/10 px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm text-text">{title}</div>
          <div className="mt-1 text-xs text-muted">{summaryParts.join(" · ")}</div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className={["rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em]", badgeClassName].join(" ")}>
            {badge}
          </div>
          <button
            type="button"
            id={toggleId}
            aria-expanded={detailsOpen}
            aria-controls={panelId}
            onClick={() => setDetailsOpen((value) => !value)}
            className="inline-flex items-center gap-1 text-xs font-medium text-muted transition hover:text-text"
          >
            {detailsOpen ? "Hide details" : "Details"}
            <ChevronRightIcon
              width={14}
              height={14}
              className={[
                "transition-transform duration-200 motion-reduce:transition-none",
                detailsOpen ? "rotate-90" : "rotate-0",
              ].join(" ")}
              aria-hidden="true"
            />
          </button>
        </div>
      </div>

      <div
        id={panelId}
        role="region"
        aria-labelledby={toggleId}
        hidden={!detailsOpen}
        className="mt-3"
      >
        {detailsOpen ? (
          <div className="rounded-xl border border-border bg-bg/15 px-3 py-3">
            <WorkflowRunDetailsBody todos={todos} sections={sections} variant="inline" />
          </div>
        ) : null}
      </div>
    </div>
  );
}

type WorkflowRunDetailsVariant = "sheet" | "inline";

function WorkflowTodoStatsGrid({ todos }: { todos: WorkflowTodoItem[] }) {
  return (
    <div className="grid grid-cols-3 gap-2 pb-1">
      <div className="rounded-xl border border-border bg-bg/35 px-3 py-2">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          Completed
        </div>
        <div className="mt-1 text-sm font-medium text-text">
          {todos.filter((todo) => todo.status === "completed").length}
        </div>
      </div>
      <div className="rounded-xl border border-border bg-bg/35 px-3 py-2">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">Blocked</div>
        <div className="mt-1 text-sm font-medium text-text">
          {todos.filter((todo) => isWorkflowTodoBlocked(todo.status)).length}
        </div>
      </div>
      <div className="rounded-xl border border-border bg-bg/35 px-3 py-2">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          Cancelled
        </div>
        <div className="mt-1 text-sm font-medium text-text">
          {todos.filter((todo) => todo.status === "cancelled").length}
        </div>
      </div>
    </div>
  );
}

function WorkflowTodoRow({ todo, index }: { todo: WorkflowTodoItem; index: number }) {
  const style = getWorkflowTodoStatusStyle(todo.status);
  const Icon = style.Icon;
  const isBlocked = isWorkflowTodoBlocked(todo.status);
  const isDone = todo.status === "completed";
  const isCancelled = todo.status === "cancelled";

  return (
    <div
      className={[
        "flex items-start justify-between gap-3 rounded-xl px-3 py-3",
        isBlocked
          ? "bg-amber-50/80 ring-1 ring-amber-200"
          : todo.status === "in_progress"
            ? "bg-accent/6 ring-1 ring-accent/15"
            : "bg-bg/35",
      ].join(" ")}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[11px] text-muted">
          <span className="font-ui">Step {index + 1}</span>
          {isDone ? <CheckIcon width={12} height={12} /> : null}
        </div>
        <div
          className={[
            "mt-1 text-sm text-text",
            isDone || isCancelled ? "opacity-70" : "opacity-100",
          ].join(" ")}
        >
          {todo.content}
        </div>
      </div>
      <span
        className={[
          "inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px]",
          style.className,
        ].join(" ")}
      >
        <Icon width={12} height={12} />
        {style.label}
      </span>
    </div>
  );
}

export function WorkflowRunDetailsBody({
  todos,
  sections,
  variant = "sheet",
}: {
  todos: WorkflowTodoItem[];
  sections: AssistantDetailEvidenceSection[];
  variant?: WorkflowRunDetailsVariant;
}) {
  if (variant === "inline") {
    const rawJson = JSON.stringify({ todos, sections }, null, 2);

    return (
      <div className="space-y-3">
        <DisclosureSection title="Overview" defaultOpen>
          <WorkflowTodoStatsGrid todos={todos} />
        </DisclosureSection>
        <DisclosureSection title="Steps" count={todos.length} defaultOpen>
          <TruncatedItemList
            items={todos}
            initialCount={6}
            getKey={(todo, index) => `${todo.content}-${index}`}
            renderItem={(todo, index) => <WorkflowTodoRow todo={todo} index={index} />}
          />
        </DisclosureSection>
        <DetailSections sections={sections} variant="inline" />
        <DisclosureSection title="Raw JSON" defaultOpen={false}>
          <div className="text-sm">
            <TruncatedText text={rawJson} initialLines={12} mono />
          </div>
        </DisclosureSection>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <WorkflowTodoStatsGrid todos={todos} />
      {todos.map((todo, index) => (
        <WorkflowTodoRow key={`${todo.content}-${index}`} todo={todo} index={index} />
      ))}
      <DetailSections sections={sections} variant="sheet" />
    </div>
  );
}

export const WorkflowTodoToolUI = makeAssistantToolUI({
  toolName: "update_workflow_todo",
  render: ({ args, result }: { args: Record<string, unknown>; result?: unknown }) => {
    const threadMessages = useAssistantState(({ thread }: any) => thread?.messages);
    const argsTodos = coerceTodos(args.todos);
    const resultTodos =
      result && typeof result === "object" && result !== null
        ? coerceTodos((result as Record<string, unknown>).todos)
        : undefined;
    const payloadTodos = argsTodos ?? resultTodos ?? [];
    const canonicalTodos = extractLatestCanonicalWorkflowTodos(threadMessages);
    const shouldPreferCanonicalTodos =
      canonicalTodos &&
      canonicalTodos.length > payloadTodos.length &&
      todosHaveOverlappingContent(payloadTodos, canonicalTodos);
    const todos = shouldPreferCanonicalTodos ? canonicalTodos : payloadTodos;
    const evidenceFromArgs = coerceDetailEvidenceSections(args.evidenceSections);
    const evidenceFromResult =
      result && typeof result === "object" && result !== null
        ? coerceDetailEvidenceSections((result as Record<string, unknown>).evidenceSections)
        : undefined;
    const evidenceFromMetadata = extractLatestCanonicalEvidenceSections(threadMessages);
    const evidenceSections = evidenceFromArgs ?? evidenceFromResult ?? evidenceFromMetadata;

    return (
      <WorkflowTodoCard
        todos={todos}
        renderGateTodos={payloadTodos}
        evidenceSections={evidenceSections}
      />
    );
  },
});
