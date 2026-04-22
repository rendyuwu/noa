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

  it("preserves reasoning parts before text", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        messages: [
          {
            id: "m1",
            role: "assistant",
            parts: [
              {
                type: "reasoning",
                summary: "Curated reasoning summary",
              },
              {
                type: "text",
                text: "Final answer",
              },
            ],
          },
        ],
      },
      { pendingCommands: [], isSending: false },
    );

    expect((converted.messages[0] as any)?.content).toEqual([
      {
        type: "reasoning",
        text: "Curated reasoning summary",
      },
      {
        type: "text",
        text: "Final answer",
      },
    ]);
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
            toolName: "whm_suspend_account",
            risk: "CHANGE",
            arguments: { key: "feature_x", value: true },
            status: "PENDING",
          },
        ],
        actionRequests: [
          {
            actionRequestId: "approval-1",
            toolName: "whm_suspend_account",
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
        toolName: "whm_suspend_account",
        risk: "CHANGE",
        arguments: { key: "feature_x", value: true },
        status: "PENDING",
      },
    ]);
    expect((converted.messages[0] as any)?.metadata?.custom?.actionRequests).toEqual([
      {
        actionRequestId: "approval-1",
        toolName: "whm_suspend_account",
        risk: "CHANGE",
        arguments: { key: "feature_x", value: true },
        status: "PENDING",
        lifecycleStatus: "requested",
      },
    ]);
  });

  it("drops exact duplicate adjacent assistant text messages as a defensive fallback", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        messages: [
          {
            id: "m1",
            role: "assistant",
            parts: [{ type: "text", text: "Same answer" }],
          },
          {
            id: "m2",
            role: "assistant",
            parts: [{ type: "text", text: "Same answer" }],
          },
        ],
      },
      { pendingCommands: [], isSending: false },
    );

    expect(converted.messages).toHaveLength(1);
    expect((converted.messages[0] as any)?.content).toEqual([
      { type: "text", text: "Same answer" },
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
                toolName: "mock_change_tool",
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

  it("treats completed backend snapshots as non-running even if transport metadata still says sending", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        runStatus: "COMPLETED" as any,
        messages: [
          {
            id: "m1",
            role: "assistant",
            parts: [{ type: "text", text: "Done" }],
          },
        ],
      },
      { pendingCommands: [], isSending: true },
    );

    expect(converted.isRunning).toBe(false);
    expect((converted.messages[0] as any)?.status).toEqual({ type: "complete", reason: "stop" });
  });

  it("keeps terminal completed snapshots running when a pending add-message command is in flight", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        runStatus: "completed",
        messages: [
          {
            id: "m1",
            role: "assistant",
            parts: [{ type: "text", text: "Done" }],
          },
        ],
      },
      {
        pendingCommands: [
          {
            type: "add-message",
            message: { role: "user", parts: [{ type: "text", text: "Follow-up" }] },
          },
        ],
        isSending: true,
      },
    );

    expect(converted.isRunning).toBe(true);
    expect((converted.messages[0] as any)?.status).toEqual({ type: "running" });
    expect(converted.messages.at(-1)?.role).toBe("user");
    expect((converted.messages.at(-1) as any)?.content).toEqual([{ type: "text", text: "Follow-up" }]);
  });

  it("treats terminal completed snapshots as non-running when only a non-add-message command is pending", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        runStatus: "completed",
        messages: [
          {
            id: "m1",
            role: "assistant",
            parts: [{ type: "text", text: "Done" }],
          },
        ],
      },
      {
        pendingCommands: [
          {
            type: "approve-action",
            actionRequestId: "approval-1",
          },
        ],
        isSending: true,
      },
    );

    expect(converted.isRunning).toBe(false);
    expect((converted.messages[0] as any)?.status).toEqual({ type: "complete", reason: "stop" });
  });

  it("treats failed backend snapshots as non-running when no pending commands exist", () => {
    const converted = convertAssistantState(
      {
        isRunning: false,
        runStatus: "failed",
        lastErrorReason: "Request failed",
        messages: [
          {
            id: "m1",
            role: "assistant",
            parts: [{ type: "text", text: "Request failed" }],
          },
        ],
      },
      { pendingCommands: [], isSending: true },
    );

    expect(converted.isRunning).toBe(false);
    expect((converted.messages[0] as any)?.status).toEqual({ type: "complete", reason: "stop" });
  });
});
