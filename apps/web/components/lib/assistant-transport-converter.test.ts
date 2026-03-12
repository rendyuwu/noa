import { describe, expect, it } from "vitest";

import { convertAssistantState } from "./assistant-transport-converter";

describe("convertAssistantState", () => {
  it("returns an empty non-running state when no messages are present", () => {
    const result = convertAssistantState(
      { messages: [], isRunning: false },
      { pendingCommands: [], isSending: false },
    );

    expect(result).toEqual({ messages: [], isRunning: false });
  });

  it("ensures tool-call parts always have argsText", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        messages: [
          {
            id: "m1",
            role: "assistant",
            parts: [
              {
                type: "tool-call",
                toolName: "get_current_time",
                toolCallId: "tool-call-1",
                args: {},
              },
            ],
          },
        ],
      },
      { pendingCommands: [], isSending: false },
    );

    const message = converted.messages[0];
    expect(message?.role).toBe("assistant");
    const toolPart = (message as any)?.content?.find?.((p: any) => p?.type === "tool-call");
    expect(typeof toolPart?.argsText).toBe("string");
    expect(toolPart?.argsText).toBe("{}");
  });
});
