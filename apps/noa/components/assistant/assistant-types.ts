import { useAssistantTransportSendCommand } from "@assistant-ui/react";

export type ApprovalCommand = {
  actionRequestId: string;
  type: "approve-action" | "deny-action";
};

/**
 * Typed wrapper around useAssistantTransportSendCommand for approval commands.
 * Avoids `as unknown as` double-cast at every call site.
 */
export function useApprovalSendCommand(): (command: ApprovalCommand) => void {
  return useAssistantTransportSendCommand() as unknown as (command: ApprovalCommand) => void;
}
