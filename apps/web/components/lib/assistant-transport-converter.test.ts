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
    expect(toolPart).toBeDefined();
    expect(typeof toolPart?.argsText).toBe("string");
    expect(toolPart?.argsText).toBe("{}");
  });

  it("uses deterministic fallback toolCallId when missing", () => {
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
    expect(toolPart).toBeDefined();
    expect(toolPart?.toolCallId).toBe("toolcall-m1-0");
    expect(toolPart?.argsText).toBe("{}");
  });

  it("merges tool-result messages onto the matching tool-call part", () => {
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
          {
            id: "m2",
            role: "tool",
            parts: [
              {
                type: "tool-result",
                toolName: "get_current_time",
                toolCallId: "tool-call-1",
                result: { time: "10:00" },
                isError: false,
              },
            ],
          },
        ],
      },
      { pendingCommands: [], isSending: false },
    );

    expect(converted.messages).toHaveLength(1);
    const toolPart = (converted.messages[0] as any).content.find((p: any) => p.type === "tool-call");
    expect(toolPart?.result).toEqual({ time: "10:00" });
    expect(toolPart?.isError).toBe(false);
  });

  it("attaches canonical workflow metadata to the latest message", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        workflow: [
          {
            content: "Request approval",
            status: "waiting_on_approval",
            priority: "high",
          },
        ],
        evidenceSections: [
          {
            title: "Execution evidence",
            items: [{ label: "Server", value: "cp01" }],
          },
        ],
        pendingApprovals: [
          {
            actionRequestId: "approval-1",
            toolName: "set_demo_flag",
            risk: "CHANGE",
            arguments: { key: "feature_x", value: true },
            status: "PENDING",
          },
        ],
        actionRequests: [
          {
            actionRequestId: "approval-1",
            toolName: "set_demo_flag",
            risk: "CHANGE",
            arguments: { key: "feature_x", value: true },
            status: "PENDING",
            lifecycleStatus: "requested",
          },
        ],
        messages: [
          {
            id: "m1",
            role: "assistant",
            parts: [{ type: "text", text: "Working on it" }],
          },
        ],
      },
      { pendingCommands: [], isSending: false },
    );

    expect((converted.messages[0] as any)?.metadata?.custom?.workflow).toEqual([
      {
        content: "Request approval",
        status: "waiting_on_approval",
        priority: "high",
      },
    ]);
    expect((converted.messages[0] as any)?.metadata?.custom?.evidenceSections).toEqual([
      {
        title: "Execution evidence",
        items: [{ label: "Server", value: "cp01" }],
      },
    ]);
    expect((converted.messages[0] as any)?.metadata?.custom?.pendingApprovals).toEqual([
      {
        actionRequestId: "approval-1",
        toolName: "set_demo_flag",
        risk: "CHANGE",
        arguments: { key: "feature_x", value: true },
        status: "PENDING",
      },
    ]);
    expect((converted.messages[0] as any)?.metadata?.custom?.actionRequests).toEqual([
      {
        actionRequestId: "approval-1",
        toolName: "set_demo_flag",
        risk: "CHANGE",
        arguments: { key: "feature_x", value: true },
        status: "PENDING",
        lifecycleStatus: "requested",
      },
    ]);
  });

  it("clears canonical metadata arrays when workflow and approvals are absent", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        messages: [
          {
            id: "m1",
            role: "assistant",
            parts: [{ type: "text", text: "All done" }],
          },
        ],
      },
      { pendingCommands: [], isSending: false },
    );

    expect((converted.messages[0] as any)?.metadata?.custom?.workflow).toEqual([]);
    expect((converted.messages[0] as any)?.metadata?.custom?.evidenceSections).toEqual([]);
    expect((converted.messages[0] as any)?.metadata?.custom?.pendingApprovals).toEqual([]);
    expect((converted.messages[0] as any)?.metadata?.custom?.actionRequests).toEqual([]);
  });

  it("drops proposal tool calls", () => {
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
                toolName: "set_demo_flag",
                toolCallId: "proposal-123",
                args: { key: "demo_flag", value: true },
              },
            ],
          },
        ],
      },
      { pendingCommands: [], isSending: false },
    );

    expect(converted.messages).toEqual([]);
  });

  it("keeps unmergeable tool-result messages when toolCallId is missing", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        messages: [
          {
            id: "m1",
            role: "tool",
            parts: [
              {
                type: "tool-result",
                toolName: "get_current_time",
                result: { time: "10:00" },
                isError: false,
              },
            ],
          },
        ],
      },
      { pendingCommands: [], isSending: false },
    );

    expect(converted.messages).toHaveLength(1);
    const toolPart = (converted.messages[0] as any).content.find((p: any) => p.type === "tool-call");
    expect(toolPart).toBeDefined();
    expect(toolPart?.result).toEqual({ time: "10:00" });
    expect(toolPart?.args).toEqual({});
    expect(toolPart?.argsText).toBe("{}");
  });
});
