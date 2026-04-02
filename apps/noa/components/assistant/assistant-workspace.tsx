import { MessageSquarePlus, Sparkles } from "lucide-react";

export function AssistantWorkspace({ threadId }: { threadId?: string | null }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      <section className="rounded-3xl border border-border bg-surface p-4 shadow-soft">
        <div className="flex items-center gap-2 text-sm font-medium text-text">
          <MessageSquarePlus className="size-4 text-accent" />
          Thread navigator
        </div>
        <p className="mt-3 font-ui text-sm leading-6 text-muted">
          The shared shell, auth guard, and same-origin API boundary are in place. This lane still needs the
          assistant-ui runtime, thread hydration, streaming transport, approvals, and workflow dock parity.
        </p>
      </section>

      <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
        <div className="flex items-center gap-2 text-sm font-medium text-text">
          <Sparkles className="size-4 text-accent" />
          Assistant workspace scaffold
        </div>
        <h2 className="mt-4 text-2xl font-semibold tracking-[-0.02em] text-text">
          {threadId ? `Thread ${threadId}` : "New conversation"}
        </h2>
        <p className="mt-3 max-w-2xl font-ui text-sm leading-6 text-muted">
          This placeholder keeps the route contract in place without pulling Claude-era UI into the rewrite. Later
          phases will replace this with assistant-ui-driven conversation state and tool rendering.
        </p>
      </section>
    </div>
  );
}
