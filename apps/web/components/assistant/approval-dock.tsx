"use client";

import { useEffect, useMemo, useState } from "react";

import { CheckIcon, ClockIcon, Cross2Icon, RocketIcon } from "@radix-ui/react-icons";
import { useAssistantTransportSendCommand } from "@assistant-ui/react";

import type { AssistantActionLifecycleStatus, AssistantActionRequest } from "@/components/assistant/approval-state";

type DecisionState = "approving" | "denying";

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
      detail: "Buttons are locked while the approval command is in flight.",
      badge: "pending",
      badgeClassName: "bg-accent/15 text-accent",
      Icon: ClockIcon,
    };
  }

  if (pendingDecision === "denying") {
    return {
      title: "Sending denial",
      detail: "Buttons are locked while the denial command is in flight.",
      badge: "pending",
      badgeClassName: "bg-amber-100 text-amber-900",
      Icon: ClockIcon,
    };
  }

  switch (status) {
    case "requested":
      return {
        title: "Approval requested",
        detail: "Review the proposed change before execution begins.",
        badge: "requested",
        badgeClassName: "bg-accent/15 text-accent",
        Icon: ClockIcon,
      };
    case "approved":
      return {
        title: "Approval recorded",
        detail: "The request is approved and about to execute.",
        badge: "approved",
        badgeClassName: "bg-emerald-100 text-emerald-900",
        Icon: CheckIcon,
      };
    case "executing":
      return {
        title: "Executing approved action",
        detail: "NOA is running the approved change now.",
        badge: "executing",
        badgeClassName: "bg-sky-100 text-sky-900",
        Icon: RocketIcon,
      };
    case "finished":
      return {
        title: "Approved action finished",
        detail: "The approved change completed successfully.",
        badge: "finished",
        badgeClassName: "bg-emerald-100 text-emerald-900",
        Icon: CheckIcon,
      };
    case "failed":
      return {
        title: "Approved action failed",
        detail: "The approval succeeded, but the execution did not finish cleanly.",
        badge: "failed",
        badgeClassName: "bg-red-50 text-red-800",
        Icon: Cross2Icon,
      };
    case "denied":
      return {
        title: "Action request denied",
        detail: "Execution stops and the request is now terminal.",
        badge: "denied",
        badgeClassName: "bg-slate-200 text-slate-800",
        Icon: Cross2Icon,
      };
  }
}

function getVisibleRequests(requests: AssistantActionRequest[]): AssistantActionRequest[] {
  const liveRequests = requests.filter(
    (request) =>
      request.lifecycleStatus === "requested" ||
      request.lifecycleStatus === "approved" ||
      request.lifecycleStatus === "executing",
  );
  if (liveRequests.length) {
    return liveRequests;
  }

  const latestRequest = requests[requests.length - 1];
  return latestRequest ? [latestRequest] : [];
}

export function ApprovalDock({ requests }: { requests: AssistantActionRequest[] }) {
  const sendCommand = useAssistantTransportSendCommand();
  const [pendingDecisions, setPendingDecisions] = useState<Record<string, DecisionState>>({});

  const visibleRequests = useMemo(() => getVisibleRequests(requests), [requests]);

  useEffect(() => {
    const requestedIds = new Set(
      requests
        .filter((request) => request.lifecycleStatus === "requested")
        .map((request) => request.actionRequestId),
    );

    setPendingDecisions((current) => {
      const nextEntries = Object.entries(current).filter(([actionRequestId]) =>
        requestedIds.has(actionRequestId),
      );
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
    <div className="mb-3 overflow-hidden rounded-xl border border-border bg-surface shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.035),0_0_0_0.5px_rgba(0,0,0,0.08)]">
      <div className="flex items-start justify-between gap-3 border-b border-border bg-surface-2 px-4 py-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-text">Action approvals</div>
          <div className="mt-0.5 text-xs text-muted">
            Canonical approval state, independent from transcript cards.
          </div>
        </div>
        <div className="shrink-0 rounded bg-surface px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-muted">
          live
        </div>
      </div>

      <div className="space-y-3 px-4 py-3">
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
              className="rounded-lg border border-border bg-bg/40 px-3 py-3"
              data-testid="approval-dock-card"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-text">{copy.title}</div>
                  <div className="mt-1 text-xs text-muted">{copy.detail}</div>
                  <div className="mt-1 text-xs text-muted">
                    Tool: <code className="text-[11px]">{request.toolName}</code>
                  </div>
                  <div className="mt-1 text-sm text-text">
                    {prettifyToolName(request.toolName)} request for action <code className="text-[11px]">{request.actionRequestId}</code>
                  </div>
                </div>
                <div className={["inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px]", copy.badgeClassName].join(" ")}>
                  <copy.Icon width={12} height={12} />
                  <span className="leading-none">{copy.badge}</span>
                </div>
              </div>

              {request.lifecycleStatus === "requested" ? (
                <div className="mt-3 flex flex-wrap gap-2">
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
                    className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg bg-accent px-3 text-xs font-medium text-white transition-colors hover:bg-accent/90 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <CheckIcon width={16} height={16} />
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
                    className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-border bg-transparent px-3 text-xs font-medium text-text transition-all hover:bg-surface-2 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Cross2Icon width={16} height={16} />
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
