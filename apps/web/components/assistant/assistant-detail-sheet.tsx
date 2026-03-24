"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Cross2Icon } from "@radix-ui/react-icons";

import {
  closeAssistantDetailSheet,
  useAssistantDetailSheet,
} from "@/components/assistant/assistant-detail-sheet-store";
import { DetailSections } from "@/components/assistant/detail-sections";
import { WorkflowRunDetailsBody } from "@/components/assistant/workflow-todo-tool-ui";

export function AssistantDetailSheet() {
  const detail = useAssistantDetailSheet();

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
                  <div className="space-y-3">
                    <DetailSections sections={detail.sections} variant="sheet" showEmptyState />
                  </div>
                ) : (
                  <WorkflowRunDetailsBody todos={detail.todos} sections={detail.sections} variant="sheet" />
                )}
              </div>
            </div>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
