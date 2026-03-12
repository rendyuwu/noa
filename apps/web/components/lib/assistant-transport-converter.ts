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

const partsToContent = (parts: Array<Record<string, unknown>>, messageId: string) => {
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
      const args = coerceRecord(part.args) ?? {};
      const rawArgsText = coerceString(part.argsText);
      const argsText = rawArgsText && rawArgsText.trim() ? rawArgsText : JSON.stringify(args);
      content.push({
        type: "tool-call",
        toolName,
        toolCallId,
        args,
        argsText,
      });
      continue;
    }

    // Server persists tool results as standalone "tool" messages.
    // assistant-ui expects tool results to live on a "tool-call" part.
    if (type === "tool-result") {
      const toolName = coerceString(part.toolName) ?? "unknown";
      const toolCallId = coerceString(part.toolCallId);
      const isError = typeof part.isError === "boolean" ? part.isError : undefined;
      content.push({
        type: "tool-call",
        toolName,
        ...(toolCallId ? { toolCallId } : {}),
        argsText: "{}",
        result: part.result,
        ...(isError !== undefined ? { isError } : {}),
      });
      continue;
    }
  }

  return content;
};

const toThreadMessage = (
  raw: { id?: string; role: string; parts: Array<Record<string, unknown>> },
  fallbackId: string,
): ThreadMessage => {
  const createdAt = new Date();
  const id = raw.id ?? fallbackId;
  const role = raw.role === "tool" ? "assistant" : raw.role;
  const content = partsToContent(raw.parts ?? [], id);

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

  const optimisticMessages: ThreadMessage[] = connectionMetadata.pendingCommands
    .filter((command) => command.type === "add-message")
    .map((command, index) =>
      toThreadMessage(
        {
          role: command.message?.role ?? "user",
          parts: command.message?.parts ?? [],
        },
        `optimistic-${index}`,
      ),
    );

  const persistedMessages: ThreadMessage[] = (state.messages ?? []).map((message, index) =>
    toThreadMessage(message, `persisted-${index}`),
  );

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
