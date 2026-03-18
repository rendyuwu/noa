"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { CheckIcon, Cross2Icon } from "@radix-ui/react-icons";

import {
  closeAssistantDetailSheet,
  type DetailSection,
  useAssistantDetailSheet,
} from "@/components/assistant/assistant-detail-sheet-store";
import {
  getWorkflowTodoStatusStyle,
  isWorkflowTodoBlocked,
} from "@/components/assistant/workflow-todo-tool-ui";

export function AssistantDetailSheet() {
  const detail = useAssistantDetailSheet();

  const renderSections = (sections: DetailSection[], showEmptyState: boolean) => {
    if (sections.length === 0) {
      if (!showEmptyState) {
        return null;
      }
      return (
        <div className="rounded-xl border border-dashed border-border bg-bg/20 px-4 py-3 text-sm text-muted">
          No structured evidence is available for this request.
        </div>
      );
    }

    return sections.map((section) => (
      <section
        key={section.title}
        className="rounded-xl border border-border bg-bg/35 px-4 py-3"
      >
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          {section.title}
        </h3>
        <dl className="mt-3 space-y-2 text-sm">
          {section.items.map((item) => (
            <div
              key={`${section.title}-${item.label}-${item.value}`}
              className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3"
            >
              <dt className="text-muted">{item.label}</dt>
              <dd className="min-w-0 break-words text-text">{item.value}</dd>
            </div>
          ))}
        </dl>
      </section>
    ));
  };

  return (
    <Dialog.Root open={detail.open} onOpenChange={(open) => (open ? undefined : closeAssistantDetailSheet())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/45 backdrop-blur-[1px]" />
        <Dialog.Content
          className={[
            "fixed inset-x-4 bottom-4 z-50 max-h-[80vh] overflow-hidden rounded-2xl border border-border bg-surface shadow-[0_1.5rem_4rem_rgba(0,0,0,0.28)] outline-none",
            "sm:inset-x-auto sm:right-4 sm:bottom-4 sm:top-4 sm:w-[32rem]",
          ].join(" ")}
        >
          {detail.open ? (
            <div className="flex h-full flex-col">
              <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-4">
                <div className="min-w-0">
                  <Dialog.Title className="text-base font-semibold text-text">{detail.title}</Dialog.Title>
                  <Dialog.Description className="mt-1 text-sm text-muted">
                    {detail.subtitle}
                  </Dialog.Description>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className={["rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em]", detail.badgeClassName].join(" ")}>
                    {detail.badge}
                  </span>
                  <Dialog.Close asChild>
                    <button
                      type="button"
                      aria-label="Close details"
                      className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-transparent text-muted transition hover:bg-surface-2 hover:text-text"
                    >
                      <Cross2Icon width={16} height={16} />
                    </button>
                  </Dialog.Close>
                </div>
              </div>

              <div className="flex-1 overflow-y-auto px-4 py-4">
                {detail.kind === "approval" ? (
                  <div className="space-y-3">{renderSections(detail.sections, true)}</div>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-3 gap-2 pb-1">
                      <div className="rounded-xl border border-border bg-bg/35 px-3 py-2">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
                          Completed
                        </div>
                        <div className="mt-1 text-sm font-medium text-text">
                          {detail.todos.filter((todo) => todo.status === "completed").length}
                        </div>
                      </div>
                      <div className="rounded-xl border border-border bg-bg/35 px-3 py-2">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
                          Blocked
                        </div>
                        <div className="mt-1 text-sm font-medium text-text">
                          {detail.todos.filter((todo) => isWorkflowTodoBlocked(todo.status)).length}
                        </div>
                      </div>
                      <div className="rounded-xl border border-border bg-bg/35 px-3 py-2">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
                          Cancelled
                        </div>
                        <div className="mt-1 text-sm font-medium text-text">
                          {detail.todos.filter((todo) => todo.status === "cancelled").length}
                        </div>
                      </div>
                    </div>
                    {detail.todos.map((todo, index) => {
                      const style = getWorkflowTodoStatusStyle(todo.status);
                      const Icon = style.Icon;
                      const isBlocked = isWorkflowTodoBlocked(todo.status);
                      const isDone = todo.status === "completed";
                      const isCancelled = todo.status === "cancelled";

                      return (
                        <div
                          key={`${todo.content}-${index}`}
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
                    })}
                    {renderSections(detail.sections, false)}
                  </div>
                )}
              </div>
            </div>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
