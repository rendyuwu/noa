"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, Clock3, X } from "lucide-react";
import { useAssistantTransportSendCommand } from "@assistant-ui/react";

import type { AssistantActionLifecycleStatus, AssistantActionRequest } from "./approval-state";

type DecisionState = "approving" | "denying";

type ApprovalCommand = {
  actionRequestId: string;
  type: "approve-action" | "deny-action";
};

function prettifyToolName(value: string): string {
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function describeLifecycle(status: AssistantActionLifecycleStatus, pendingDecision?: DecisionState) {
  if (pendingDecision === "approving") {
    return {
      title: "Sending approval",
      badge: "pending",
      badgeClassName: "bg-accent/15 text-accent",
      Icon: Clock3,
    };
  }

  if (pendingDecision === "denying") {
    return {
      title: "Sending denial",
      badge: "pending",
      badgeClassName: "bg-amber-100 text-amber-900",
      Icon: Clock3,
    };
  }

  switch (status) {
    case "requested":
      return {
        title: "Needs approval",
        badge: "requested",
        badgeClassName: "bg-accent/15 text-accent",
        Icon: Clock3,
      };
    case "approved":
      return {
        title: "Approved",
        badge: "approved",
        badgeClassName: "bg-emerald-100 text-emerald-900",
        Icon: Check,
      };
    case "executing":
      return {
        title: "Executing",
        badge: "running",
        badgeClassName: "bg-sky-100 text-sky-900",
        Icon: Clock3,
      };
    case "finished":
      return {
        title: "Finished",
        badge: "done",
        badgeClassName: "bg-surface-2 text-muted",
        Icon: Check,
      };
    case "failed":
      return {
        title: "Failed",
        badge: "failed",
        badgeClassName: "bg-red-100 text-red-900",
        Icon: X,
      };
    case "denied":
      return {
        title: "Denied",
        badge: "denied",
        badgeClassName: "bg-surface-2 text-muted",
        Icon: X,
      };
  }
}

function getVisibleRequests(requests: AssistantActionRequest[]): AssistantActionRequest[] {
  return requests.filter(
    (request) =>
      request.lifecycleStatus === "requested" ||
      request.lifecycleStatus === "approved" ||
      request.lifecycleStatus === "executing",
  );
}

function getHeaderCopy(requests: AssistantActionRequest[]) {
  const requestedCount = requests.filter((request) => request.lifecycleStatus === "requested").length;
  if (requestedCount > 0) {
    return requestedCount === 1 ? "1 approval needed" : `${requestedCount} approvals needed`;
  }

  const executingCount = requests.filter((request) => request.lifecycleStatus === "executing").length;
  if (executingCount > 0) {
    return executingCount === 1 ? "1 approval running" : `${executingCount} approvals running`;
  }

  return requests.length === 1 ? "1 approval" : `${requests.length} approvals`;
}

export function ApprovalDock({ requests }: { requests: AssistantActionRequest[] }) {
  const sendCommand = useAssistantTransportSendCommand() as unknown as (command: ApprovalCommand) => void;
  const [collapsed, setCollapsed] = useState(true);
  const [pendingDecisions, setPendingDecisions] = useState<Record<string, DecisionState>>({});

  const visibleRequests = useMemo(() => getVisibleRequests(requests), [requests]);
  const previewRequest = visibleRequests[0];

  useEffect(() => {
    const requestedIds = new Set(
      requests
        .filter((request) => request.lifecycleStatus === "requested")
        .map((request) => request.actionRequestId),
    );

    setPendingDecisions((current) => {
      const nextEntries = Object.entries(current).filter(([actionRequestId]) => requestedIds.has(actionRequestId));
      if (nextEntries.length === Object.keys(current).length) {
        return current;
      }

      return Object.fromEntries(nextEntries) as Record<string, DecisionState>;
    });
  }, [requests]);

  if (!visibleRequests.length) {
    return null;
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-surface shadow-soft">
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/70"
      >
        <div className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-accent" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-text">{getHeaderCopy(visibleRequests)}</div>
          <div className="mt-1 truncate font-ui text-sm text-muted">
            {previewRequest ? prettifyToolName(previewRequest.toolName) : "Waiting for a decision."}
          </div>
        </div>
        <div className="inline-flex shrink-0 items-center gap-2">
          <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-accent">
            live
          </span>
          <ChevronDown
            className={[
              "size-4 text-muted transition-transform duration-200",
              collapsed ? "rotate-0" : "rotate-180",
            ].join(" ")}
          />
        </div>
      </button>

      <div className={collapsed ? "hidden" : "space-y-2 border-t border-border px-3 py-3"}>
        {visibleRequests.map((request) => {
          const pendingDecision =
            request.lifecycleStatus === "requested"
              ? pendingDecisions[request.actionRequestId]
              : undefined;
          const copy = describeLifecycle(request.lifecycleStatus, pendingDecision);
          const canAct = request.lifecycleStatus === "requested" && !pendingDecision;

          return (
            <div
              key={request.actionRequestId}
              className="rounded-xl bg-bg/40 px-3 py-2.5"
              data-testid="approval-dock-card"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-text">{prettifyToolName(request.toolName)}</div>
                  <div className="mt-1 text-xs text-muted">
                    {copy.title} <span aria-hidden="true">·</span>{" "}
                    <code className="text-[11px] text-muted">{request.actionRequestId}</code>
                  </div>
                </div>
                <div
                  className={[
                    "inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px]",
                    copy.badgeClassName,
                  ].join(" ")}
                >
                  <copy.Icon className="size-3" />
                  <span className="leading-none">{copy.badge}</span>
                </div>
              </div>

              {request.lifecycleStatus === "requested" ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={!canAct}
                    onClick={() => {
                      setPendingDecisions((current) => ({
                        ...current,
                        [request.actionRequestId]: "approving",
                      }));
                      sendCommand({ type: "approve-action", actionRequestId: request.actionRequestId });
                    }}
                    className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg bg-accent px-3 text-xs font-medium text-white transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Check className="size-3.5" />
                    {pendingDecision === "approving" ? "Approving..." : "Approve"}
                  </button>
                  <button
                    type="button"
                    disabled={!canAct}
                    onClick={() => {
                      setPendingDecisions((current) => ({
                        ...current,
                        [request.actionRequestId]: "denying",
                      }));
                      sendCommand({ type: "deny-action", actionRequestId: request.actionRequestId });
                    }}
                    className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-border bg-transparent px-3 text-xs font-medium text-text transition-all hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <X className="size-3.5" />
                    {pendingDecision === "denying" ? "Denying..." : "Deny"}
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
