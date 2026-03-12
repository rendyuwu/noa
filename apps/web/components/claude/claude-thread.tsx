"use client";

import type { FC, ReactNode } from "react";
import { useMemo, useRef, useState } from "react";

import {
  ActionBarPrimitive,
  AssistantIf,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAssistantApi,
  useAssistantState,
} from "@assistant-ui/react";
import {
  ArrowUpIcon,
  ChevronDownIcon,
  ClipboardIcon,
  HamburgerMenuIcon,
  HandIcon,
  MixerHorizontalIcon,
  PlusIcon,
  ReloadIcon,
} from "@radix-ui/react-icons";

import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import {
  formatClaudeGreetingName,
  getClaudeTimeGreeting,
} from "@/components/claude/claude-greeting";
import { ClaudeToolFallback, ClaudeToolGroup } from "@/components/claude/request-approval-tool-ui";
import { getAuthUser } from "@/components/lib/auth-store";
import { useThreadHydration } from "@/components/lib/thread-hydration";

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

const LANDING_PROMPTS = [
  { label: "Code", text: "Help me write code for..." },
  { label: "Write", text: "Help me write..." },
  { label: "Learn", text: "Teach me about..." },
  { label: "Life stuff", text: "I want advice about..." },
  { label: "Claude's choice", text: "What would you suggest I do next?" },
];

function ComposerControlsRow() {
  return (
    <div className="flex w-full items-center gap-2">
      <div className="relative flex min-w-0 flex-1 shrink items-center gap-2">
        <DisabledIconButton
          label="Add attachment"
          className="flex h-8 min-w-8 items-center justify-center overflow-hidden rounded-lg border border-border bg-transparent px-1.5 text-muted transition-all hover:bg-surface-2 hover:text-text active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
        >
          <PlusIcon width={16} height={16} />
        </DisabledIconButton>

        <DisabledIconButton
          label="Open tools menu"
          className="flex h-8 min-w-8 items-center justify-center overflow-hidden rounded-lg border border-border bg-transparent px-1.5 text-muted transition-all hover:bg-surface-2 hover:text-text active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
        >
          <MixerHorizontalIcon width={16} height={16} />
        </DisabledIconButton>

        <DisabledIconButton
          label="Extended thinking"
          className="flex h-8 min-w-8 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-border bg-transparent px-1.5 text-muted transition-all hover:bg-surface-2 hover:text-text active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
        >
          <ReloadIcon width={16} height={16} />
        </DisabledIconButton>
      </div>

      <DisabledIconButton
        label="Model selector"
        className="flex h-8 min-w-16 items-center justify-center gap-1 whitespace-nowrap rounded-md px-2 pr-2 pl-2.5 text-text text-xs transition duration-300 ease-[cubic-bezier(0.165,0.85,0.45,1)] hover:bg-surface-2 active:scale-[0.985] disabled:pointer-events-none disabled:opacity-70"
      >
        <span className="font-serif text-[14px]">Sonnet 4.5</span>
        <ChevronDownIcon width={20} height={20} className="opacity-75" />
      </DisabledIconButton>

      <ComposerPrimitive.Send
        aria-label="Send message"
        className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent transition-colors hover:bg-accent/90 active:scale-95 disabled:pointer-events-none disabled:opacity-50"
      >
        <ArrowUpIcon width={16} height={16} className="text-white" />
      </ComposerPrimitive.Send>
    </div>
  );
}

function EmptyLanding() {
  const api = useAssistantApi();
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const [activeChip, setActiveChip] = useState<string | null>(null);

  const user = useMemo(() => getAuthUser(), []);
  const name = useMemo(() => formatClaudeGreetingName(user), [user]);
  const timeGreeting = useMemo(() => getClaudeTimeGreeting(), []);

  const setPrompt = (label: string, text: string) => {
    setActiveChip(label);
    api.composer().setText(text);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  return (
    <div className="flex min-h-full w-full flex-1 flex-col items-center justify-center px-3 py-10">
      <div className="claude-landing-anim-1 flex items-center gap-3 text-text">
        <span aria-hidden="true" className="text-2xl leading-none text-accent">
          *
        </span>
        <h1 className="text-center text-4xl font-medium tracking-[-0.02em] sm:text-5xl">
          {timeGreeting}, {name}
        </h1>
      </div>

      <div className="claude-landing-anim-2 mt-8 w-full max-w-2xl">
        <ComposerPrimitive.Root className="flex w-full max-w-2xl flex-col rounded-2xl border border-border bg-surface p-0.5 shadow-md">
          <div className="m-4 flex flex-col gap-3.5">
            <div className="wrap-break-word max-h-96 w-full overflow-y-auto">
              <ComposerPrimitive.Input
                ref={inputRef}
                placeholder="How can I help you today?"
                aria-label="Message input"
                className="block min-h-10 w-full resize-none bg-transparent text-text outline-none placeholder:text-muted"
              />
            </div>

            <ComposerControlsRow />
          </div>
        </ComposerPrimitive.Root>
      </div>

      <div className="claude-landing-anim-3 mt-4 w-full max-w-2xl overflow-x-auto">
        <div className="flex w-max items-center gap-2 px-1 pb-1">
          {LANDING_PROMPTS.map((chip) => (
            <button
              key={chip.label}
              type="button"
              onClick={() => setPrompt(chip.label, chip.text)}
              aria-pressed={activeChip === chip.label}
              className={[
                "inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 px-3 py-1.5 font-ui text-sm text-text shadow-sm transition",
                "hover:bg-surface active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/35 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
                activeChip === chip.label ? "border-accent/40" : "",
              ].join(" ")}
              title="Prefill prompt"
            >
              {chip.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ThreadHydrationSkeleton() {
  return (
    <div
      role="status"
      aria-label="Loading conversation"
      className="flex min-h-full w-full flex-1 flex-col px-4 py-10"
    >
      <div className="mx-auto w-full max-w-3xl">
        <div className="flex justify-end">
          <div className="h-10 w-40 animate-pulse rounded-2xl bg-surface-2" />
        </div>

        <div className="mt-12 space-y-3">
          <div className="h-5 w-5/6 animate-pulse rounded-lg bg-surface-2" />
          <div className="h-5 w-full animate-pulse rounded-lg bg-surface-2" />
          <div className="h-5 w-11/12 animate-pulse rounded-lg bg-surface-2" />
          <div className="h-5 w-4/6 animate-pulse rounded-lg bg-surface-2" />
          <div className="h-5 w-10/12 animate-pulse rounded-lg bg-surface-2" />
          <div className="h-5 w-3/6 animate-pulse rounded-lg bg-surface-2" />
        </div>
      </div>

      <div className="mt-auto w-full">
        <div className="mx-auto h-16 w-full max-w-3xl animate-pulse rounded-2xl bg-surface-2" />
      </div>
    </div>
  );
}

export const ClaudeThread: FC<{
  onOpenSidebar?: () => void;
  showOpenSidebarButtonOnDesktop?: boolean;
}> = ({ onOpenSidebar, showOpenSidebarButtonOnDesktop }) => {
  const { isHydrating } = useThreadHydration();
  const threadStatus = useAssistantState(({ threadListItem }: any) => threadListItem?.status);
  const showHydrationSkeleton = Boolean(isHydrating) && threadStatus !== "new";

  const sidebarButtonClassName = [
    "absolute top-3 left-3 z-10 flex items-center gap-2",
    showOpenSidebarButtonOnDesktop ? "" : "md:hidden",
  ].join(" ");

  return (
    <ThreadPrimitive.Root className="relative flex h-full min-h-0 flex-col items-stretch bg-bg p-4 pt-14 font-serif">
      {onOpenSidebar ? (
        <div className={sidebarButtonClassName}>
          <button
            type="button"
            onClick={onOpenSidebar}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-surface/70 text-muted shadow-sm backdrop-blur-sm transition hover:bg-surface hover:text-text active:scale-[0.98]"
            aria-label="Open sidebar"
          >
            <HamburgerMenuIcon width={18} height={18} />
          </button>
        </div>
      ) : null}

      <ThreadPrimitive.Viewport
        autoScroll
        scrollToBottomOnRunStart
        scrollToBottomOnInitialize
        scrollToBottomOnThreadSwitch
        data-testid="thread-viewport"
        data-auto-scroll="true"
        className="min-h-0 flex grow flex-col overflow-y-auto"
      >
        <ThreadPrimitive.Empty>
          {showHydrationSkeleton ? <ThreadHydrationSkeleton /> : <EmptyLanding />}
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ Message: ChatMessage }} />
        <div aria-hidden="true" className="h-4" />
      </ThreadPrimitive.Viewport>

      <AssistantIf condition={({ thread }) => !thread.isEmpty}>
        <ComposerPrimitive.Root className="mx-auto flex w-full max-w-3xl flex-col rounded-2xl border border-border bg-surface p-0.5 shadow-sm transition-shadow duration-200 hover:shadow-md focus-within:shadow-md">
          <div className="m-3.5 flex flex-col gap-3.5">
            <div className="relative">
              <div className="wrap-break-word max-h-96 w-full overflow-y-auto">
                <ComposerPrimitive.Input
                  placeholder="How can I help you today?"
                  aria-label="Message input"
                  className="block min-h-6 w-full resize-none bg-transparent text-text outline-none placeholder:text-muted"
                />
              </div>
            </div>

            <ComposerControlsRow />
          </div>
        </ComposerPrimitive.Root>
      </AssistantIf>
    </ThreadPrimitive.Root>
  );
};

function ClaudeThinkingIndicator() {
  return (
    <div
      role="status"
      aria-label="Claude is thinking"
      className="inline-flex items-center gap-2 text-muted text-sm"
    >
      <span
        aria-hidden="true"
        className="h-2 w-2 animate-pulse rounded-full bg-current opacity-70"
      />
      <span
        aria-hidden="true"
        className="h-2 w-2 animate-pulse rounded-full bg-current opacity-40 [animation-delay:150ms]"
      />
      <span
        aria-hidden="true"
        className="h-2 w-2 animate-pulse rounded-full bg-current opacity-25 [animation-delay:300ms]"
      />
      <span className="font-ui">Thinking...</span>
    </div>
  );
}

const ChatMessage: FC = () => {
  const showLoading = useAssistantState(({ message }: any) => {
    if (message?.role !== "assistant") return false;
    if (message?.status?.type !== "running") return false;
    const content = Array.isArray(message.content) ? message.content : [];
    return content.every((part: any) => {
      if (part?.type !== "text") return true;
      return typeof part.text === "string" ? part.text.trim() === "" : true;
    });
  });

  const UserText = ({ text }: any) => {
    return <span className="whitespace-pre-wrap">{text}</span>;
  };

  return (
    <MessagePrimitive.Root className="group relative mx-auto mt-1 mb-1 block w-full max-w-3xl">
      <AssistantIf condition={(s) => s.message.role === "user"}>
        <div className="flex w-full justify-end">
          <div
            data-testid="user-message"
            className="ml-auto max-w-[75ch] rounded-2xl bg-surface-2 px-4 py-3 text-text shadow-sm ring-1 ring-border/40"
          >
            <div className="wrap-break-word">
              <MessagePrimitive.Parts components={{ Text: UserText }} />
            </div>
          </div>
        </div>
      </AssistantIf>

      <AssistantIf condition={(s) => s.message.role === "assistant"}>
        <div className="relative mb-12 font-serif">
          <div className="relative leading-[1.65rem]">
            <div className="grid grid-cols-1 gap-2.5">
              <div className="wrap-break-word whitespace-normal pr-8 pl-2 font-serif text-text">
                {showLoading ? <ClaudeThinkingIndicator /> : null}
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
              <div className="flex items-center text-muted">
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
                <p className="mt-2 w-full text-right text-muted text-[0.65rem] leading-[0.85rem] opacity-90 sm:text-[0.75rem]">
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
