"use client";

import { useEffect, useMemo, useState } from "react";

import type { WorkflowTodoItem } from "./workflow-todo-tool-ui";
import { isWorkflowTodoBlocked } from "./workflow-todo-tool-ui";

export type WorkflowDockPhase = "hide" | "open" | "blocked" | "close" | "clear";

type WorkflowDockStateOptions = {
  todos: WorkflowTodoItem[];
  isRunning: boolean;
  closeDelayMs?: number;
};

export function useWorkflowDockState({
  todos,
  isRunning,
  closeDelayMs = 400,
}: WorkflowDockStateOptions) {
  const [phase, setPhase] = useState<WorkflowDockPhase>("hide");
  const [isMounted, setIsMounted] = useState(false);

  const hasTodos = todos.length > 0;
  const hasBlockedTodo = useMemo(() => todos.some((todo) => isWorkflowTodoBlocked(todo.status)), [todos]);
  const hasIncompleteTodo = useMemo(
    () => todos.some((todo) => todo.status !== "completed" && todo.status !== "cancelled"),
    [todos],
  );
  const isLive = isRunning || hasBlockedTodo;

  useEffect(() => {
    if (!hasTodos) {
      setPhase("hide");
      setIsMounted(false);
      return;
    }

    if (isLive) {
      setPhase(hasBlockedTodo ? "blocked" : "open");
      setIsMounted(true);
      return;
    }

    if (hasIncompleteTodo) {
      setPhase("clear");
      setIsMounted(false);
      return;
    }

    setPhase("close");
    setIsMounted(true);

    const timeoutId = window.setTimeout(() => {
      setPhase("hide");
      setIsMounted(false);
    }, closeDelayMs);

    return () => window.clearTimeout(timeoutId);
  }, [closeDelayMs, hasBlockedTodo, hasIncompleteTodo, hasTodos, isLive]);

  return {
    phase,
    isMounted,
    isBlocked: phase === "blocked",
  };
}
