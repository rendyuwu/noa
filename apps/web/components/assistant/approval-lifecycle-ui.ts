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
        badgeClassName: "bg-emerald-100/80 text-emerald-900",
        Icon: CheckIcon,
      };
    case "executing":
      return {
        title: "Applying approved change",
        detail: "NOA is running the approved change now.",
        badge: "running",
        badgeClassName: "bg-sky-100/80 text-sky-900",
        Icon: RocketIcon,
      };
    case "finished":
      return {
        title: "Change complete",
        detail: "The approved change completed successfully.",
        badge: "done",
        badgeClassName: "bg-emerald-100/80 text-emerald-900",
        Icon: CheckIcon,
      };
    case "failed":
      return {
        title: "Change failed",
        detail: "The approval succeeded, but the execution did not finish cleanly.",
        badge: "failed",
        badgeClassName: "bg-rose-100/80 text-rose-900",
        Icon: Cross2Icon,
      };
    case "denied":
      return {
        title: "Change denied",
        detail: "Execution stops and the request is now terminal.",
        badge: "denied",
        badgeClassName: "bg-slate-200/80 text-slate-800",
        Icon: Cross2Icon,
      };
  }
}
