"use client";

import { ComposerPrimitive, MessagePrimitive, ThreadPrimitive, useAssistantState } from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { AlertTriangle, ArrowUp, LoaderCircle, RefreshCw, Square } from "lucide-react";
import remarkGfm from "remark-gfm";

import { useThreadHydration } from "@/components/lib/runtime/thread-hydration";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import { ToolFallback, ToolGroup } from "./assistant-tool-ui";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="mb-4 flex justify-end">
      <div className="max-w-[85%] rounded-2xl bg-accent px-4 py-3 text-sm text-accent-foreground shadow-sm">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function MarkdownText() {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      className="aui-md-root text-text [&_a]:text-accent [&_code]:rounded [&_code]:bg-bg [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-sm [&_h1]:mb-2 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mb-1.5 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-1 [&_h3]:text-sm [&_h3]:font-semibold [&_li]:ml-4 [&_ol]:my-1 [&_ol]:list-decimal [&_p]:my-1 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-border [&_pre]:bg-bg [&_pre]:p-3 [&_pre_code]:bg-bg [&_pre_code]:p-0 [&_ul]:my-1 [&_ul]:list-disc"
    />
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="mb-4">
      <div className="max-w-[92%] text-sm text-text">
        <MessagePrimitive.Parts
          components={{
            Text: MarkdownText,
            ToolGroup,
            tools: { Fallback: ToolFallback },
          }}
        />
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
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted">
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
            <div className="flex min-h-[50vh] flex-col items-center justify-center text-center">
              <h2 className="text-2xl font-semibold tracking-tight text-text">
                How can I help you?
              </h2>
              <p className="mt-2 max-w-md font-ui text-sm text-muted">
                Start a conversation with NOA. Your threads are saved automatically.
              </p>
            </div>
          </ThreadPrimitive.Empty>

          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />

          {isRunning && (
            <div className="flex items-center gap-2 py-2 text-sm text-muted">
              <LoaderCircle className="size-3.5 animate-spin" />
              Thinking…
            </div>
          )}
        </div>
      </ThreadPrimitive.Viewport>

      {/* Composer pinned to bottom */}
      <div className="border-t border-border/50 bg-bg/80 backdrop-blur">
        <ComposerPrimitive.Root className="mx-auto w-full max-w-3xl px-4 py-3">
          <div className="flex items-end gap-2 rounded-2xl border border-border bg-surface p-2 shadow-sm">
            <ComposerPrimitive.Input
              className="min-h-[44px] flex-1 resize-none border-0 bg-transparent px-3 py-2 font-ui text-sm text-text outline-none placeholder:text-muted"
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
        </ComposerPrimitive.Root>
      </div>
    </ThreadPrimitive.Root>
  );
}
