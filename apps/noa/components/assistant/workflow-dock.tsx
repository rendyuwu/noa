"use client";

import { useMemo, useState } from "react";
import { ChevronDown, Clock3, Dot } from "lucide-react";

import type { WorkflowTodoItem } from "./workflow-todo-tool-ui";
import { getWorkflowTodoStatusStyle, isWorkflowTodoBlocked } from "./workflow-todo-tool-ui";
import { useWorkflowDockState } from "./workflow-dock-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";

function getActiveTodoIndex(todos: WorkflowTodoItem[]): number {
  const inProgressIndex = todos.findIndex((todo) => todo.status === "in_progress");
  if (inProgressIndex >= 0) {
    return inProgressIndex;
  }

  const blockedIndex = todos.findIndex((todo) => isWorkflowTodoBlocked(todo.status));
  if (blockedIndex >= 0) {
    return blockedIndex;
  }

  const pendingIndex = todos.findIndex((todo) => todo.status === "pending");
  if (pendingIndex >= 0) {
    return pendingIndex;
  }

  for (let index = todos.length - 1; index >= 0; index -= 1) {
    if (todos[index]?.status === "completed") {
      return index;
    }
  }

  return todos.length ? 0 : -1;
}

function getDockCopy(phase: ReturnType<typeof useWorkflowDockState>["phase"]) {
  if (phase === "blocked") {
    return {
      title: "Workflow paused",
      badge: "blocked",
      badgeVariant: "warning" as const,
    };
  }

  if (phase === "close") {
    return {
      title: "Workflow complete",
      badge: "done",
      badgeVariant: "muted" as const,
    };
  }

  return {
    title: "Workflow",
    badge: "live",
    badgeVariant: "info" as const,
  };
}

function getProgressLabel(todos: WorkflowTodoItem[]): string {
  const done = todos.filter((todo) => todo.status === "completed").length;
  return `${done}/${todos.length}`;
}

function getPreview(todo: WorkflowTodoItem | undefined): string {
  if (!todo) {
    return "No steps recorded.";
  }

  if (isWorkflowTodoBlocked(todo.status)) {
    return `Waiting: ${todo.content}`;
  }

  if (todo.status === "completed") {
    return `Last done: ${todo.content}`;
  }

  return todo.content;
}

export function WorkflowDock({ todos, isRunning }: { todos: WorkflowTodoItem[]; isRunning: boolean }) {
  const { phase, isBlocked, isMounted } = useWorkflowDockState({ todos, isRunning });
  const [collapsed, setCollapsed] = useState(true);
  const activeTodoIndex = getActiveTodoIndex(todos);
  const activeTodo = activeTodoIndex >= 0 ? todos[activeTodoIndex] : undefined;
  const preview = useMemo(() => getPreview(activeTodo), [activeTodo]);

  if (!isMounted || !todos.length) {
    return null;
  }

  const copy = getDockCopy(phase);

  return (
    <div
      className={[
        "overflow-hidden rounded-2xl border border-border bg-surface shadow-soft transition-all duration-200",
        phase === "close" ? "opacity-75" : "opacity-100",
      ].join(" ")}
      data-testid="workflow-todo-dock"
      data-workflow-phase={phase}
      aria-live="polite"
      aria-label="Workflow progress"
    >
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        aria-expanded={!collapsed}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/70"
      >
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div
            className={[
              "mt-1 h-2.5 w-2.5 shrink-0 rounded-full",
              phase === "close" ? "bg-surface-2" : isBlocked ? "bg-warning" : "bg-accent",
            ].join(" ")}
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm text-text">
              <span className="font-semibold">{copy.title}</span>
              <Badge variant="muted" className="px-2 py-0.5 font-ui text-[11px]">
                {getProgressLabel(todos)}
              </Badge>
            </div>
            <div className="mt-1 truncate font-ui text-sm text-muted">{preview}</div>
          </div>
        </div>
        <div className="inline-flex shrink-0 items-center gap-2">
          <Badge variant={copy.badgeVariant} className="text-[10px] uppercase tracking-[0.08em]">
            {copy.badge}
          </Badge>
          <ChevronDown
            className={[
              "size-4 text-muted transition-transform duration-200",
              collapsed ? "rotate-0" : "rotate-180",
            ].join(" ")}
          />
        </div>
      </button>

      <div className={collapsed ? "hidden" : "border-t border-border px-3 pb-3"}>
        {isBlocked ? (
          <Alert tone="warning" className="mt-3">
            <Clock3 />
            <div>
              <AlertTitle>Workflow paused</AlertTitle>
              <AlertDescription>Waiting on approval or user input before continuing.</AlertDescription>
            </div>
          </Alert>
        ) : null}
        <div className="max-h-44 overflow-y-auto pt-3">
          <ul className="space-y-1.5">
            {todos.map((todo, index) => {
              const style = getWorkflowTodoStatusStyle(todo.status);
              const isActive = index === activeTodoIndex;
              const isTodoBlocked = isWorkflowTodoBlocked(todo.status);
              return (
                <li
                  key={`${todo.content}-${index}`}
                  className={[
                    "flex items-start justify-between gap-3 rounded-xl px-3 py-2 transition-colors",
                    isActive
                      ? isTodoBlocked
                        ? "bg-warning/10 ring-1 ring-warning/30"
                        : "bg-accent/6 ring-1 ring-accent/15"
                      : "bg-bg/40",
                  ].join(" ")}
                  data-testid={isActive ? "workflow-active-step" : undefined}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 text-[11px] text-muted">
                      <Dot className={isTodoBlocked ? "size-3.5 text-warning" : "size-3.5 text-muted"} />
                      <span className="font-ui">
                        {isActive ? (isTodoBlocked ? "Waiting" : "Current") : `Step ${index + 1}`}
                      </span>
                    </div>
                    <div className="mt-1 pr-2 text-sm text-text">{todo.content}</div>
                  </div>
                  <Badge variant={style.variant} className="gap-1.5 text-[10px] uppercase tracking-[0.08em]">
                    <style.Icon className="size-3" />
                    <span className="leading-none">{style.label}</span>
                  </Badge>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </div>
  );
}
