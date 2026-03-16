"use client";

import { DotFilledIcon } from "@radix-ui/react-icons";

import type { WorkflowTodoItem } from "@/components/assistant/workflow-todo-tool-ui";
import {
  getWorkflowTodoStatusStyle,
  isWorkflowTodoBlocked,
} from "@/components/assistant/workflow-todo-tool-ui";
import { useWorkflowDockState } from "@/components/assistant/workflow-dock-state";

function getActiveTodoIndex(todos: WorkflowTodoItem[]): number {
  const inProgressIndex = todos.findIndex((todo) => todo.status === "in_progress");
  if (inProgressIndex >= 0) return inProgressIndex;

  const blockedIndex = todos.findIndex((todo) => isWorkflowTodoBlocked(todo.status));
  if (blockedIndex >= 0) return blockedIndex;

  const pendingIndex = todos.findIndex((todo) => todo.status === "pending");
  if (pendingIndex >= 0) return pendingIndex;

  for (let index = todos.length - 1; index >= 0; index -= 1) {
    if (todos[index]?.status === "completed") return index;
  }

  return todos.length ? 0 : -1;
}

function getDockCopy(phase: ReturnType<typeof useWorkflowDockState>["phase"]) {
  if (phase === "blocked") {
    return {
      eyebrow: "Workflow paused",
      detail: "Waiting for input before the next step can continue.",
    };
  }

  if (phase === "close") {
    return {
      eyebrow: "Workflow complete",
      detail: "All recorded steps finished successfully.",
    };
  }

  return {
    eyebrow: "Workflow running",
    detail: "Live checklist from the canonical assistant state.",
  };
}

export function WorkflowDock({ todos, isRunning }: { todos: WorkflowTodoItem[]; isRunning: boolean }) {
  const { phase, isBlocked, isMounted } = useWorkflowDockState({ todos, isRunning });
  const activeTodoIndex = getActiveTodoIndex(todos);

  if (!isMounted || !todos.length) {
    return null;
  }

  const copy = getDockCopy(phase);

  return (
    <div
      className={[
        "mb-3 overflow-hidden rounded-xl border border-border bg-surface shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.035),0_0_0_0.5px_rgba(0,0,0,0.08)] transition-all duration-200",
        phase === "close" ? "opacity-70" : "opacity-100",
      ].join(" ")}
      data-testid="workflow-todo-dock"
      data-workflow-phase={phase}
    >
      <div className="flex items-start justify-between gap-3 border-b border-border bg-surface-2 px-4 py-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-text">{copy.eyebrow}</div>
          <div className="mt-0.5 text-xs text-muted">{copy.detail}</div>
        </div>
        <div
          className={[
            "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em]",
            isBlocked ? "bg-amber-100 text-amber-900" : "bg-accent/15 text-accent",
          ].join(" ")}
        >
          {isBlocked ? "blocked" : phase === "close" ? "done" : "live"}
        </div>
      </div>

      <div className="px-4 py-3">
        <ul className="space-y-2">
          {todos.map((todo, index) => {
            const style = getWorkflowTodoStatusStyle(todo.status);
            const isActive = index === activeTodoIndex;
            const isTodoBlocked = isWorkflowTodoBlocked(todo.status);
            return (
              <li
                key={`${todo.content}-${index}`}
                className={[
                  "flex items-start justify-between gap-3 rounded-lg border px-3 py-2 transition-colors",
                  isActive
                    ? isTodoBlocked
                      ? "border-amber-300 bg-amber-50/70"
                      : "border-accent/30 bg-accent/5"
                    : "border-border bg-bg/40",
                ].join(" ")}
                data-testid={isActive ? "workflow-active-step" : undefined}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
                    <DotFilledIcon width={14} height={14} />
                    {isActive ? (isTodoBlocked ? "Blocked step" : "Current step") : `Step ${index + 1}`}
                  </div>
                  <div className="mt-1 text-sm text-text">{todo.content}</div>
                </div>
                <div
                  className={[
                    "shrink-0 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px]",
                    style.className,
                  ].join(" ")}
                >
                  <style.Icon width={12} height={12} />
                  <span className="leading-none">{style.label}</span>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
