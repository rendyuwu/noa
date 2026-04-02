import { describe, expect, it } from "vitest";

import { convertAssistantState } from "./assistant-transport-converter";

describe("convertAssistantState", () => {
  it("merges tool results into the matching assistant tool call", () => {
    const state = convertAssistantState(
      {
        isRunning: false,
        messages: [
          {
            id: "assistant-1",
            role: "assistant",
            parts: [
              {
                type: "tool-call",
                toolName: "request_approval",
                toolCallId: "call-1",
                args: { actionRequestId: "req-1" },
              },
            ],
          },
          {
            id: "tool-1",
            role: "tool",
            parts: [
              {
                type: "tool-result",
                toolName: "request_approval",
                toolCallId: "call-1",
                result: { approved: true },
              },
            ],
          },
        ],
      },
      { pendingCommands: [], isSending: false },
    );

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]?.content).toEqual([
      expect.objectContaining({
        type: "tool-call",
        toolCallId: "call-1",
        result: { approved: true },
      }),
    ]);
  });

  it("marks the latest assistant message as running while transport is sending", () => {
    const state = convertAssistantState(
      {
        isRunning: false,
        messages: [
          {
            id: "assistant-1",
            role: "assistant",
            parts: [{ type: "text", text: "Working…" }],
          },
        ],
      },
      { pendingCommands: [], isSending: true },
    );

    expect(state.isRunning).toBe(true);
    expect(state.messages[0]?.status).toEqual({ type: "running" });
  });
});
