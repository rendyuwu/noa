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
});
