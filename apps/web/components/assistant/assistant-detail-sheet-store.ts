"use client";

import { useSyncExternalStore } from "react";

import type { WorkflowTodoItem } from "@/components/assistant/workflow-todo-tool-ui";

export type DetailItem = {
  label: string;
  value: string;
};

export type DetailSection = {
  title: string;
  items: DetailItem[];
};

type ClosedDetailSheet = {
  open: false;
};

type ApprovalDetailSheet = {
  open: true;
  key: string;
  kind: "approval";
  title: string;
  subtitle: string;
  badge: string;
  badgeClassName: string;
  sections: DetailSection[];
};

type WorkflowDetailSheet = {
  open: true;
  key: string;
  kind: "workflow";
  title: string;
  subtitle: string;
  badge: string;
  badgeClassName: string;
  todos: WorkflowTodoItem[];
};

export type AssistantDetailSheetState =
  | ClosedDetailSheet
  | ApprovalDetailSheet
  | WorkflowDetailSheet;

let state: AssistantDetailSheetState = { open: false };

const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function setState(next: AssistantDetailSheetState) {
  state = next;
  emit();
}

export function closeAssistantDetailSheet() {
  setState({ open: false });
}

export function toggleAssistantDetailSheet(next: Exclude<AssistantDetailSheetState, ClosedDetailSheet>) {
  if (state.open && state.key === next.key) {
    closeAssistantDetailSheet();
    return;
  }

  setState(next);
}

export function useAssistantDetailSheet() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => state,
    () => state,
  );
}
