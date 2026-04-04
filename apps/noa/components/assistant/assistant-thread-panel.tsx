"use client";

import { ComposerPrimitive, MessagePrimitive, ThreadPrimitive, useAssistantState } from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { AlertTriangle, Bot, LoaderCircle, MessageSquarePlus, RefreshCw } from "lucide-react";
import remarkGfm from "remark-gfm";

import { useThreadHydration } from "@/components/lib/runtime/thread-hydration";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import { ToolFallback, ToolGroup } from "./assistant-tool-ui";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="mb-3 flex justify-end">
      <div className="max-w-[85%] rounded-2xl bg-accent px-4 py-3 text-sm text-accent-foreground shadow-soft">
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
    <MessagePrimitive.Root className="mb-3">
      <div className="max-w-[92%] rounded-2xl border border-border bg-surface px-4 py-3 text-sm text-text shadow-soft">
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
    <ThreadPrimitive.Root className="flex min-h-[65vh] flex-col rounded-3xl border border-border bg-surface shadow-soft">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Bot className="size-4 text-accent" />
        <div className="text-sm font-medium text-text">Assistant workspace</div>
        {(isHydrating || isRunning) && (
          <div className="ml-auto inline-flex items-center gap-2 font-ui text-xs text-muted">
            <LoaderCircle className="size-3.5 animate-spin" />
            {isHydrating ? "Hydrating thread…" : "Assistant running…"}
          </div>
        )}
      </div>

      <ThreadPrimitive.Viewport className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {errorMessage ? (
          <Alert tone="destructive" className="mb-4">
            <AlertTriangle />
            <div>
              <AlertTitle>Thread recovery failed</AlertTitle>
              <AlertDescription>{errorMessage}</AlertDescription>
              <Button variant="outline" size="sm" className="mt-3 gap-2 rounded-xl font-ui text-sm font-medium" onClick={retry}>
                <RefreshCw className="size-4" />
                Retry restore
              </Button>
            </div>
          </Alert>
        ) : null}

        <ThreadPrimitive.Empty>
          <div className="mx-auto max-w-xl rounded-3xl border border-dashed border-border bg-bg/70 p-8 text-center">
            <MessageSquarePlus className="mx-auto size-10 text-accent" />
            <h2 className="mt-4 text-2xl font-semibold tracking-[-0.02em] text-text">Start a new conversation</h2>
            <p className="mt-3 font-ui text-sm leading-6 text-muted">
              Start a conversation with your AI assistant. Your threads are saved and synced automatically.
            </p>
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
      </ThreadPrimitive.Viewport>

      <ComposerPrimitive.Root className="border-t border-border px-4 py-4">
        <div className="rounded-2xl border border-border bg-bg p-2 shadow-sm">
          <ComposerPrimitive.Input
            className="min-h-24 w-full resize-none border-0 bg-bg px-3 py-2 font-ui text-sm text-text outline-none"
            placeholder="Ask NOA…"
          />
          <div className="mt-2 flex items-center justify-end gap-2">
            <ComposerPrimitive.Cancel asChild type="button">
              <Button variant="outline" size="sm" className="rounded-xl font-ui text-sm font-medium">
                Stop
              </Button>
            </ComposerPrimitive.Cancel>
            <ComposerPrimitive.Send asChild type="submit">
              <Button size="sm" className="rounded-xl font-ui text-sm font-semibold">
                Send
              </Button>
            </ComposerPrimitive.Send>
          </div>
        </div>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
}
