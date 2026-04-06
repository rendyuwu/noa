import "@assistant-ui/react";

declare module "@assistant-ui/react" {
  namespace Assistant {
    interface Commands {
      approveAction: {
        type: "approve-action";
        actionRequestId: string;
      };
      denyAction: {
        type: "deny-action";
        actionRequestId: string;
      };
    }
  }
}
