"use client";

import type { FC, ReactNode } from "react";

import {
  ActionBarPrimitive,
  AssistantIf,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import * as Avatar from "@radix-ui/react-avatar";
import {
  ArrowUpIcon,
  ChevronDownIcon,
  ClipboardIcon,
  HamburgerMenuIcon,
  HandIcon,
  MixerHorizontalIcon,
  Pencil1Icon,
  PlusIcon,
  ReloadIcon,
} from "@radix-ui/react-icons";

import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { ClaudeToolFallback, ClaudeToolGroup } from "@/components/claude/request-approval-tool-ui";

function DisabledIconButton({
  children,
  className,
  label,
}: {
  children: ReactNode;
  className: string;
  label: string;
}) {
  return (
    <button
      type="button"
      disabled
      aria-disabled="true"
      title="Coming soon"
      aria-label={label}
      className={className}
    >
      {children}
    </button>
  );
}

export const ClaudeThread: FC<{ onOpenSidebar?: () => void }> = ({ onOpenSidebar }) => {
  return (
    <ThreadPrimitive.Root className="relative flex h-full flex-col items-stretch bg-[#F5F5F0] p-4 pt-14 font-serif dark:bg-[#2b2a27]">
      <div className="absolute top-3 left-3 z-10 flex items-center gap-2 md:hidden">
        <button
          type="button"
          onClick={onOpenSidebar}
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#00000015] bg-white/70 text-[#6b6a68] shadow-sm backdrop-blur-sm transition hover:bg-white hover:text-[#1a1a18] active:scale-[0.98] dark:border-[#6c6a6040] dark:bg-[#1f1e1b]/70 dark:text-[#9a9893] dark:hover:bg-[#1f1e1b] dark:hover:text-[#eee]"
          aria-label="Open sidebar"
        >
          <HamburgerMenuIcon width={18} height={18} />
        </button>
      </div>

      <ThreadPrimitive.Viewport className="flex grow flex-col overflow-y-scroll">
        <ThreadPrimitive.Empty>
          <div className="mx-auto mt-8 w-full max-w-3xl px-2 text-[#6b6a68] dark:text-[#9a9893]">
            Start a new conversation.
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ Message: ChatMessage }} />
        <div aria-hidden="true" className="h-4" />
      </ThreadPrimitive.Viewport>

      <ComposerPrimitive.Root className="mx-auto flex w-full max-w-3xl flex-col rounded-2xl border border-transparent bg-white p-0.5 shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.035),0_0_0_0.5px_rgba(0,0,0,0.08)] transition-shadow duration-200 focus-within:shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.075),0_0_0_0.5px_rgba(0,0,0,0.15)] hover:shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.05),0_0_0_0.5px_rgba(0,0,0,0.12)] dark:bg-[#1f1e1b] dark:shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.4),0_0_0_0.5px_rgba(108,106,96,0.15)] dark:hover:shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.4),0_0_0_0.5px_rgba(108,106,96,0.3)] dark:focus-within:shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.5),0_0_0_0.5px_rgba(108,106,96,0.3)]">
        <div className="m-3.5 flex flex-col gap-3.5">
          <div className="relative">
            <div className="wrap-break-word max-h-96 w-full overflow-y-auto">
              <ComposerPrimitive.Input
                placeholder="How can I help you today?"
                aria-label="Message input"
                className="block min-h-6 w-full resize-none bg-transparent text-[#1a1a18] outline-none placeholder:text-[#9a9893] dark:text-[#eee] dark:placeholder:text-[#9a9893]"
              />
            </div>
          </div>

          <div className="flex w-full items-center gap-2">
            <div className="relative flex min-w-0 flex-1 shrink items-center gap-2">
              <DisabledIconButton
                label="Add attachment"
                className="flex h-8 min-w-8 items-center justify-center overflow-hidden rounded-lg border border-[#00000015] bg-transparent px-1.5 text-[#6b6a68] transition-all hover:bg-[#f5f5f0] hover:text-[#1a1a18] active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 dark:border-[#6c6a6040] dark:text-[#9a9893] dark:hover:bg-[#393937] dark:hover:text-[#eee]"
              >
                <PlusIcon width={16} height={16} />
              </DisabledIconButton>

              <DisabledIconButton
                label="Open tools menu"
                className="flex h-8 min-w-8 items-center justify-center overflow-hidden rounded-lg border border-[#00000015] bg-transparent px-1.5 text-[#6b6a68] transition-all hover:bg-[#f5f5f0] hover:text-[#1a1a18] active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 dark:border-[#6c6a6040] dark:text-[#9a9893] dark:hover:bg-[#393937] dark:hover:text-[#eee]"
              >
                <MixerHorizontalIcon width={16} height={16} />
              </DisabledIconButton>

              <DisabledIconButton
                label="Extended thinking"
                className="flex h-8 min-w-8 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-[#00000015] bg-transparent px-1.5 text-[#6b6a68] transition-all hover:bg-[#f5f5f0] hover:text-[#1a1a18] active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 dark:border-[#6c6a6040] dark:text-[#9a9893] dark:hover:bg-[#393937] dark:hover:text-[#eee]"
              >
                <ReloadIcon width={16} height={16} />
              </DisabledIconButton>
            </div>

            <DisabledIconButton
              label="Model selector"
              className="flex h-8 min-w-16 items-center justify-center gap-1 whitespace-nowrap rounded-md px-2 pr-2 pl-2.5 text-[#1a1a18] text-xs transition duration-300 ease-[cubic-bezier(0.165,0.85,0.45,1)] hover:bg-[#f5f5f0] active:scale-[0.985] disabled:pointer-events-none disabled:opacity-70 dark:text-[#eee] dark:hover:bg-[#393937]"
            >
              <span className="font-serif text-[14px]">Sonnet 4.5</span>
              <ChevronDownIcon width={20} height={20} className="opacity-75" />
            </DisabledIconButton>

            <ComposerPrimitive.Send
              aria-label="Send message"
              className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#ae5630] transition-colors hover:bg-[#c4633a] active:scale-95 disabled:pointer-events-none disabled:opacity-50 dark:bg-[#ae5630] dark:hover:bg-[#c4633a]"
            >
              <ArrowUpIcon width={16} height={16} className="text-white" />
            </ComposerPrimitive.Send>
          </div>
        </div>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
};

const ChatMessage: FC = () => {
  return (
    <MessagePrimitive.Root className="group relative mx-auto mt-1 mb-1 block w-full max-w-3xl">
      <AssistantIf condition={(s) => s.message.role === "user"}>
        <div className="group/user wrap-break-word relative inline-flex max-w-[75ch] flex-col gap-2 rounded-xl bg-[#DDD9CE] py-2.5 pr-6 pl-2.5 text-[#1a1a18] transition-all dark:bg-[#393937] dark:text-[#eee]">
          <div className="relative flex flex-row gap-2">
            <div className="shrink-0 self-start transition-all duration-300">
              <Avatar.Root className="flex h-7 w-7 shrink-0 select-none items-center justify-center rounded-full bg-[#1a1a18] font-bold text-[12px] text-white dark:bg-[#eee] dark:text-[#2b2a27]">
                <Avatar.Fallback>U</Avatar.Fallback>
              </Avatar.Root>
            </div>
            <div className="flex-1">
              <div className="relative grid grid-cols-1 gap-2 py-0.5">
                <div className="wrap-break-word whitespace-pre-wrap">
                  <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
                </div>
              </div>
            </div>
          </div>

          <div className="pointer-events-none absolute right-2 bottom-0">
            <div className="pointer-events-auto min-w-max translate-x-1 translate-y-4 rounded-lg border-[#00000015] border-[0.5px] bg-white/80 p-0.5 opacity-0 shadow-sm backdrop-blur-sm transition group-hover/user:translate-x-0.5 group-hover/user:opacity-100 dark:border-[#6c6a6040] dark:bg-[#1f1e1b]/80">
              <div className="flex items-center text-[#6b6a68] dark:text-[#9a9893]">
                <DisabledIconButton
                  label="Reload"
                  className="flex h-8 w-8 items-center justify-center rounded-md transition duration-300 ease-[cubic-bezier(0.165,0.85,0.45,1)] hover:bg-transparent active:scale-95 disabled:pointer-events-none disabled:opacity-60"
                >
                  <ReloadIcon width={20} height={20} />
                </DisabledIconButton>
                <DisabledIconButton
                  label="Edit"
                  className="flex h-8 w-8 items-center justify-center rounded-md transition duration-300 ease-[cubic-bezier(0.165,0.85,0.45,1)] hover:bg-transparent active:scale-95 disabled:pointer-events-none disabled:opacity-60"
                >
                  <Pencil1Icon width={20} height={20} />
                </DisabledIconButton>
              </div>
            </div>
          </div>
        </div>
      </AssistantIf>

      <AssistantIf condition={(s) => s.message.role === "assistant"}>
        <div className="relative mb-12 font-serif">
          <div className="relative leading-[1.65rem]">
            <div className="grid grid-cols-1 gap-2.5">
              <div className="wrap-break-word whitespace-normal pr-8 pl-2 font-serif text-[#1a1a18] dark:text-[#eee]">
                <MessagePrimitive.Parts
                  components={{
                    Text: MarkdownText,
                    ToolGroup: ClaudeToolGroup,
                    tools: { Fallback: ClaudeToolFallback },
                  }}
                />
              </div>
            </div>
          </div>

          <div className="pointer-events-none absolute inset-x-0 bottom-0">
            <ActionBarPrimitive.Root
              hideWhenRunning
              autohide="not-last"
              className="pointer-events-auto flex w-full translate-y-4 flex-col items-end px-2 pt-2 opacity-0 transition group-hover:translate-y-0 group-hover:opacity-100 group-focus-within:translate-y-0 group-focus-within:opacity-100"
            >
              <div className="flex items-center text-[#6b6a68] dark:text-[#9a9893]">
                <ActionBarPrimitive.Copy
                  aria-label="Copy message"
                  className="flex h-8 w-8 items-center justify-center rounded-md transition duration-300 ease-[cubic-bezier(0.165,0.85,0.45,1)] hover:bg-transparent active:scale-95"
                >
                  <ClipboardIcon width={20} height={20} />
                </ActionBarPrimitive.Copy>

                <DisabledIconButton
                  label="Thumbs up"
                  className="flex h-8 w-8 items-center justify-center rounded-md transition duration-300 ease-[cubic-bezier(0.165,0.85,0.45,1)] hover:bg-transparent active:scale-95 disabled:pointer-events-none disabled:opacity-60"
                >
                  <HandIcon width={18} height={18} />
                </DisabledIconButton>
                <DisabledIconButton
                  label="Thumbs down"
                  className="flex h-8 w-8 items-center justify-center rounded-md transition duration-300 ease-[cubic-bezier(0.165,0.85,0.45,1)] hover:bg-transparent active:scale-95 disabled:pointer-events-none disabled:opacity-60"
                >
                  <HandIcon width={18} height={18} className="rotate-180" />
                </DisabledIconButton>
                <DisabledIconButton
                  label="Reload"
                  className="flex h-8 w-8 items-center justify-center rounded-md transition duration-300 ease-[cubic-bezier(0.165,0.85,0.45,1)] hover:bg-transparent active:scale-95 disabled:pointer-events-none disabled:opacity-60"
                >
                  <ReloadIcon width={20} height={20} />
                </DisabledIconButton>
              </div>

              <AssistantIf condition={(s) => s.message.isLast}>
                <p className="mt-2 w-full text-right text-[#8a8985] text-[0.65rem] leading-[0.85rem] opacity-90 sm:text-[0.75rem] dark:text-[#b8b5a9]">
                  Claude can make mistakes. Please double-check responses.
                </p>
              </AssistantIf>
            </ActionBarPrimitive.Root>
          </div>
        </div>
      </AssistantIf>
    </MessagePrimitive.Root>
  );
};
