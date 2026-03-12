import type { ThreadMessage } from "@assistant-ui/react";

export type AssistantState = {
  messages: Array<{ id?: string; role: string; parts: Array<Record<string, unknown>> }>;
  isRunning: boolean;
};

const coerceString = (value: unknown): string | undefined => {
  return typeof value === "string" ? value : undefined;
};

const coerceRecord = (value: unknown): Record<string, unknown> | undefined => {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
};

type ToolResultData = {
  result: unknown;
  isError: boolean | undefined;
  artifact: unknown;
};

const partsToContent = (
  parts: Array<Record<string, unknown>>,
  messageId: string,
  toolResultsByCallId: Map<string, ToolResultData>,
) => {
  const content: Array<Record<string, unknown>> = [];

  for (const [partIndex, part] of parts.entries()) {
    const type = coerceString(part.type);
    if (!type) continue;

    if (type === "text") {
      const text = coerceString(part.text) ?? "";
      content.push({ type: "text", text });
      continue;
    }

    if (type === "image") {
      const image = coerceString(part.image);
      if (image) content.push({ type: "image", image });
      continue;
    }

    if (type === "tool-call") {
      const toolName = coerceString(part.toolName) ?? "unknown";
      const toolCallId = coerceString(part.toolCallId) ?? `toolcall-${messageId}-${partIndex}`;
      if (toolCallId.startsWith("proposal-")) {
        continue;
      }
      const args = coerceRecord(part.args) ?? {};
      const rawArgsText = coerceString(part.argsText);
      const argsText = rawArgsText && rawArgsText.trim() ? rawArgsText : JSON.stringify(args);
      const toolResultData = toolResultsByCallId.get(toolCallId);
      content.push({
        type: "tool-call",
        toolName,
        toolCallId,
        args,
        argsText,
        ...(toolResultData ?? {}),
      });
      continue;
    }
  }

  return content;
};

const toThreadMessage = (
  raw: { id?: string; role: string; parts: Array<Record<string, unknown>> },
  fallbackId: string,
  toolResultsByCallId: Map<string, ToolResultData>,
): ThreadMessage => {
  const createdAt = new Date();
  const id = raw.id ?? fallbackId;
  const role = raw.role === "tool" ? "assistant" : raw.role;
  const content = partsToContent(raw.parts ?? [], id, toolResultsByCallId);

  if (role === "user") {
    return {
      id,
      createdAt,
      role: "user" as const,
      content: content as any,
      attachments: [],
      metadata: { custom: {} },
    } as ThreadMessage;
  }

  // Treat any non-user role as assistant.
  return {
    id,
    createdAt,
    role: "assistant" as const,
    content: content as any,
    status: { type: "complete", reason: "stop" },
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {},
    },
  } as ThreadMessage;
};

export function convertAssistantState(
  state: AssistantState,
  connectionMetadata: { pendingCommands: Array<any>; isSending: boolean },
) {
  const transportIsRunning = Boolean(state.isRunning) || connectionMetadata.isSending;
  const toolResultsByCallId = new Map<string, ToolResultData>();

  for (const message of state.messages ?? []) {
    if (message.role !== "tool") {
      continue;
    }

    for (const part of message.parts ?? []) {
      if (coerceString(part.type) !== "tool-result") {
        continue;
      }

      const toolCallId = coerceString(part.toolCallId);
      if (!toolCallId) {
        continue;
      }

      toolResultsByCallId.set(toolCallId, {
        result: part.result,
        isError: typeof part.isError === "boolean" ? part.isError : undefined,
        artifact: part.artifact,
      });
    }
  }

  const optimisticMessages: ThreadMessage[] = connectionMetadata.pendingCommands
    .filter((command) => command.type === "add-message")
    .map((command, index) =>
      toThreadMessage(
        {
          role: command.message?.role ?? "user",
          parts: command.message?.parts ?? [],
        },
        `optimistic-${index}`,
        toolResultsByCallId,
      ),
    );

  const persistedMessages: ThreadMessage[] = (state.messages ?? [])
    .filter((message) => {
      if (message.role !== "tool") {
        return true;
      }

      return !(message.parts ?? []).some((part) => coerceString(part.type) === "tool-result");
    })
    .map((message, index) => toThreadMessage(message, `persisted-${index}`, toolResultsByCallId));

  if (transportIsRunning) {
    for (let index = persistedMessages.length - 1; index >= 0; index -= 1) {
      const message = persistedMessages[index];
      if (message?.role !== "assistant") continue;
      persistedMessages[index] = {
        ...message,
        status: { type: "running" },
      };
      break;
    }
  }

  return {
    messages: [...persistedMessages, ...optimisticMessages],
    isRunning: transportIsRunning,
  };
}
