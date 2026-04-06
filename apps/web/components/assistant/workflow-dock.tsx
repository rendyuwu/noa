"use client";

import { useMemo, useState } from "react";

import { ChevronDownIcon, DotFilledIcon } from "@radix-ui/react-icons";

import type { WorkflowTodoItem } from "@/components/assistant/workflow-todo-tool-ui";
import {
  getWorkflowTodoStatusStyle,
  isWorkflowTodoBlocked,
} from "@/components/assistant/workflow-todo-tool-ui";
import { useWorkflowDockState } from "@/components/assistant/workflow-dock-state";
import { ScrollArea } from "@/components/lib/scroll-area";

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
      title: "Workflow paused",
      badge: "blocked",
    };
  }

  if (phase === "close") {
    return {
      title: "Workflow complete",
      badge: "done",
    };
  }

  return {
    title: "Workflow",
    badge: "live",
  };
}

function getProgressLabel(todos: WorkflowTodoItem[]): string {
  const done = todos.filter((todo) => todo.status === "completed").length;
  return `${done}/${todos.length}`;
}

function getPreview(todo: WorkflowTodoItem | undefined): string {
  if (!todo) return "No steps recorded.";
  if (isWorkflowTodoBlocked(todo.status)) return `Waiting: ${todo.content}`;
  if (todo.status === "completed") return `Last done: ${todo.content}`;
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
        "overflow-hidden rounded-2xl border border-border bg-card/96 shadow-md transition-all duration-200",
        phase === "close" ? "opacity-75" : "opacity-100",
      ].join(" ")}
      data-testid="workflow-todo-dock"
      data-workflow-phase={phase}
    >
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-accent/70"
      >
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div
            className={[
              "mt-1 h-2.5 w-2.5 shrink-0 rounded-full",
              phase === "close" ? "bg-accent" : isBlocked ? "bg-warning" : "bg-primary",
            ].join(" ")}
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm text-foreground">
              <span className="font-semibold">{copy.title}</span>
              <span className="rounded-full bg-accent px-2 py-0.5 font-sans text-[11px] text-muted-foreground">
                {getProgressLabel(todos)}
              </span>
            </div>
            <div className="mt-1 truncate font-sans text-sm text-muted-foreground">{preview}</div>
          </div>
        </div>
        <div
          className={[
            "inline-flex shrink-0 items-center gap-2",
          ].join(" ")}
        >
          <span
            className={[
              "rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em]",
              isBlocked
                ? "bg-warning/15 text-warning-foreground"
                : phase === "close"
                  ? "bg-accent text-muted-foreground"
                  : "bg-primary/15 text-primary",
            ].join(" ")}
          >
            {copy.badge}
          </span>
          <ChevronDownIcon
            width={16}
            height={16}
            className={[
              "text-muted-foreground transition-transform duration-200",
              collapsed ? "rotate-0" : "rotate-180",
            ].join(" ")}
          />
        </div>
      </button>

      <div className={collapsed ? "hidden" : "border-t border-border px-3 pb-3"}>
        <ScrollArea className="w-full" viewportClassName="max-h-44">
          <ul className="space-y-1.5 pt-3">
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
                        ? "bg-warning/10 ring-1 ring-warning/25"
                        : "bg-primary/6 ring-1 ring-primary/15"
                      : "bg-background/40",
                  ].join(" ")}
                  data-testid={isActive ? "workflow-active-step" : undefined}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <DotFilledIcon width={14} height={14} />
                      <span className="font-sans">
                        {isActive ? (isTodoBlocked ? "Waiting" : "Current") : `Step ${index + 1}`}
                      </span>
                    </div>
                    <div className="mt-1 pr-2 text-sm text-foreground">{todo.content}</div>
                  </div>
                  <div
                    className={[
                      "shrink-0 inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px]",
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
        </ScrollArea>
      </div>
    </div>
  );
}
