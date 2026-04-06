import {
  CheckIcon,
  ClockIcon,
  Cross2Icon,
  RocketIcon,
} from "@radix-ui/react-icons";
import type { ComponentType } from "react";

import type { AssistantActionLifecycleStatus } from "@/components/assistant/approval-state";

type IconComponent = ComponentType<{ width?: number; height?: number; className?: string }>;

export type ApprovalLifecyclePresentation = {
  title: string;
  detail: string;
  badge: string;
  badgeClassName: string;
  Icon: IconComponent;
};

export function getApprovalLifecyclePresentation(
  status: AssistantActionLifecycleStatus,
): ApprovalLifecyclePresentation {
  switch (status) {
    case "requested":
      return {
        title: "Approval needed",
        detail: "Review the proposed change before execution begins.",
        badge: "needs approval",
        badgeClassName: "bg-accent/15 text-accent",
        Icon: ClockIcon,
      };
    case "approved":
      return {
        title: "Approval recorded",
        detail: "The request is approved and about to execute.",
        badge: "approved",
        badgeClassName: "bg-emerald-500/10 text-emerald-200 ring-1 ring-emerald-500/25",
        Icon: CheckIcon,
      };
    case "executing":
      return {
        title: "Applying approved change",
        detail: "NOA is running the approved change now.",
        badge: "running",
        badgeClassName: "bg-sky-500/10 text-sky-200 ring-1 ring-sky-500/25",
        Icon: RocketIcon,
      };
    case "finished":
      return {
        title: "Change complete",
        detail: "Execution finished. Review the outcome in the thread.",
        badge: "done",
        badgeClassName: "bg-emerald-500/10 text-emerald-200 ring-1 ring-emerald-500/25",
        Icon: CheckIcon,
      };
    case "failed":
      return {
        title: "Change failed",
        detail: "The approval succeeded, but the execution did not finish cleanly.",
        badge: "failed",
        badgeClassName: "bg-rose-500/10 text-rose-200 ring-1 ring-rose-500/25",
        Icon: Cross2Icon,
      };
    case "denied":
      return {
        title: "Change denied",
        detail: "Execution stops and the request is now terminal.",
        badge: "denied",
        badgeClassName: "bg-slate-500/10 text-slate-200 ring-1 ring-slate-500/25",
        Icon: Cross2Icon,
      };
  }
}
