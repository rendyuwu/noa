"use client";

import { ComposerPrimitive, MessagePrimitive, ThreadPrimitive, useAssistantState, useMessage } from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { AlertTriangle, ArrowUp, LoaderCircle, Paperclip, RefreshCw, Square } from "lucide-react";
import remarkGfm from "remark-gfm";

import { useThreadHydration } from "@/components/lib/runtime/thread-hydration";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { EmptyState } from "./empty-state";
import { MessageActions } from "./message-actions";
import { ToolFallback, ToolGroup } from "./assistant-tool-ui";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="mb-4 flex justify-end">
      <div className="max-w-[85%] rounded-2xl bg-accent px-4 py-3 font-ui text-sm text-accent-foreground shadow-sm">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function MarkdownText() {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      className="aui-md-root font-ui text-text [&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 [&_code]:rounded [&_code]:bg-surface-2 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-sm [&_h1]:mb-2 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mb-1.5 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-1 [&_h3]:text-sm [&_h3]:font-semibold [&_li]:ml-4 [&_ol]:my-1 [&_ol]:list-decimal [&_p]:my-1 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-border [&_pre]:bg-surface-2 [&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_ul]:my-1 [&_ul]:list-disc"
    />
  );
}

function AssistantMessageWithActions() {
  const msg = useMessage();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- assistant-ui message shape varies by role
  const parts = (msg as any).content as Array<{ type: string; text?: string }> | undefined;
  const textContent = parts
    ?.filter((p) => p.type === "text" && typeof p.text === "string")
    .map((p) => p.text!)
    .join("\n") ?? "";

  return (
    <MessagePrimitive.Root className="group/msg mb-4">
      <div className="max-w-[92%] font-ui text-sm text-text">
        <MessagePrimitive.Parts
          components={{
            Text: MarkdownText,
            ToolGroup,
            tools: { Fallback: ToolFallback },
          }}
        />
        <div className="mt-1">
          <MessageActions content={textContent} />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}

export function ThreadPanel() {
  const { errorMessage, isHydrating, retry } = useThreadHydration();
  const isRunning = useAssistantState(({ thread }) => Boolean(thread?.isRunning));

  return (
    <ThreadPrimitive.Root className="flex flex-1 flex-col">
      {/* Scrollable message area */}
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6">
          {isHydrating && (
            <div className="flex items-center justify-center gap-2 py-12 font-ui text-sm text-muted">
              <LoaderCircle className="size-4 animate-spin" />
              Restoring conversation…
            </div>
          )}

          {errorMessage && !isHydrating ? (
            <Alert tone="destructive" className="mb-4">
              <AlertTriangle />
              <div>
                <AlertTitle>Thread recovery failed</AlertTitle>
                <AlertDescription>{errorMessage}</AlertDescription>
                <Button variant="outline" size="sm" className="mt-3 gap-2 rounded-xl font-ui text-sm font-medium" onClick={retry}>
                  <RefreshCw className="size-4" />
                  Retry
                </Button>
              </div>
            </Alert>
          ) : null}

          <ThreadPrimitive.Empty>
            <EmptyState />
          </ThreadPrimitive.Empty>

          <ThreadPrimitive.Messages
            components={{ UserMessage, AssistantMessage: AssistantMessageWithActions }}
          />

          {isRunning && (
            <div className="flex items-center gap-2 py-2 font-ui text-sm text-muted">
              <span className="flex gap-1">
                <span className="size-2 animate-bounce rounded-full bg-accent/60 [animation-delay:0ms]" />
                <span className="size-2 animate-bounce rounded-full bg-accent/60 [animation-delay:150ms]" />
                <span className="size-2 animate-bounce rounded-full bg-accent/60 [animation-delay:300ms]" />
              </span>
              NOA is thinking…
            </div>
          )}
        </div>
      </ThreadPrimitive.Viewport>

      {/* Composer pinned to bottom */}
      <div className="border-t border-border/40 bg-bg/80 backdrop-blur">
        <ComposerPrimitive.Root className="mx-auto w-full max-w-3xl px-4 py-3">
          <div className="flex items-end gap-2 rounded-2xl border border-border bg-surface p-2 shadow-sm transition-shadow focus-within:border-accent/30 focus-within:shadow-md">
            {/* Attachment placeholder */}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-9 shrink-0 rounded-xl text-muted"
                  disabled
                  aria-label="Attach file"
                >
                  <Paperclip className="size-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Coming soon</TooltipContent>
            </Tooltip>

            <ComposerPrimitive.Input
              className="min-h-[44px] flex-1 resize-none border-0 bg-transparent px-2 py-2 font-ui text-sm text-text outline-none placeholder:text-muted"
              placeholder="Ask NOA…"
            />
            {isRunning ? (
              <ComposerPrimitive.Cancel asChild type="button">
                <Button
                  size="icon"
                  variant="outline"
                  className="size-9 shrink-0 rounded-xl"
                  aria-label="Stop"
                >
                  <Square className="size-4" />
                </Button>
              </ComposerPrimitive.Cancel>
            ) : (
              <ComposerPrimitive.Send asChild type="submit">
                <Button
                  size="icon"
                  className="size-9 shrink-0 rounded-xl"
                  aria-label="Send"
                >
                  <ArrowUp className="size-4" />
                </Button>
              </ComposerPrimitive.Send>
            )}
          </div>
          <p className="mt-1.5 text-center font-ui text-[11px] text-muted/50">
            NOA can make mistakes. Verify important information.
          </p>
        </ComposerPrimitive.Root>
      </div>
    </ThreadPrimitive.Root>
  );
}
