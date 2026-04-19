import type { ThreadMessage } from "@assistant-ui/react";

import type { WorkflowTodoItem } from "@/components/assistant/workflow-todo-tool-ui";
import type {
  AssistantActionRequest,
  AssistantDetailEvidenceSection,
} from "@/components/assistant/approval-state";

type AssistantPendingApproval = {
  actionRequestId: string;
  toolName: string;
  risk: string;
  arguments: Record<string, unknown>;
  status: string;
};

export type AssistantState = {
  messages: Array<{ id?: string; role: string; parts: Array<Record<string, unknown>> }>;
  workflow?: WorkflowTodoItem[];
  evidenceSections?: AssistantDetailEvidenceSection[];
  pendingApprovals?: AssistantPendingApproval[];
  actionRequests?: AssistantActionRequest[];
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

const isEmptyAssistantMessage = (message: ThreadMessage): boolean => {
  return message.role === "assistant" && Array.isArray(message.content) && message.content.length === 0;
};

const singleAssistantText = (message: ThreadMessage): string | undefined => {
  if (message.role !== "assistant" || !Array.isArray(message.content) || message.content.length !== 1) {
    return undefined;
  }

  const [part] = message.content as Array<Record<string, unknown>>;
  return part?.type === "text" && typeof part.text === "string" ? part.text : undefined;
};

const dedupeAdjacentAssistantTextMessages = (messages: ThreadMessage[]) => {
  const deduped: ThreadMessage[] = [];

  for (const message of messages) {
    const previous = deduped[deduped.length - 1];
    const currentText = singleAssistantText(message);
    const previousText = previous ? singleAssistantText(previous) : undefined;

    if (currentText && previousText === currentText) {
      continue;
    }

    deduped.push(message);
  }

  return deduped;
};

const attachCanonicalMetadata = (
  messages: ThreadMessage[],
  state: Pick<AssistantState, "workflow" | "evidenceSections" | "pendingApprovals" | "actionRequests">,
) => {
  if (!messages.length) {
    return messages;
  }

  const lastMessage = messages[messages.length - 1] as ThreadMessage & {
    metadata?: Record<string, unknown>;
  };

  const lastMetadata = coerceRecord(lastMessage.metadata) ?? {};
  const custom = coerceRecord(lastMetadata.custom) ?? {};

  const nextMessages = [...messages];
  nextMessages[messages.length - 1] = {
    ...lastMessage,
    metadata: {
      ...lastMetadata,
      custom: {
        ...custom,
        workflow: Array.isArray(state.workflow) ? state.workflow : [],
        evidenceSections: Array.isArray(state.evidenceSections) ? state.evidenceSections : [],
        pendingApprovals: Array.isArray(state.pendingApprovals) ? state.pendingApprovals : [],
        actionRequests: Array.isArray(state.actionRequests) ? state.actionRequests : [],
      },
    },
  } as ThreadMessage;

  return nextMessages;
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

    if (type === "reasoning") {
      const text = coerceString(part.summary) ?? coerceString(part.text) ?? "";
      if (!text) continue;

      const parentId = coerceString(part.parentId);
      content.push({
        type: "reasoning",
        text,
        ...(parentId ? { parentId } : {}),
      });
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

    if (type === "tool-result") {
      const toolName = coerceString(part.toolName) ?? "unknown";
      const toolCallId = coerceString(part.toolCallId);
      const isError = typeof part.isError === "boolean" ? part.isError : undefined;
      content.push({
        type: "tool-call",
        toolName,
        ...(toolCallId ? { toolCallId } : {}),
        args: {},
        argsText: "{}",
        result: part.result,
        ...(isError !== undefined ? { isError } : {}),
        ...(part.artifact !== undefined ? { artifact: part.artifact } : {}),
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
  const mergeableToolCallIds = new Set<string>();
  const toolResultsByCallId = new Map<string, ToolResultData>();

  for (const message of state.messages ?? []) {
    if (message.role === "tool") {
      continue;
    }

    for (const part of message.parts ?? []) {
      if (coerceString(part.type) !== "tool-call") {
        continue;
      }

      const toolCallId = coerceString(part.toolCallId);
      if (toolCallId) {
        mergeableToolCallIds.add(toolCallId);
      }
    }
  }

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
      if (!mergeableToolCallIds.has(toolCallId)) {
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

      const parts = message.parts ?? [];
      const hasToolResult = parts.some((part) => coerceString(part.type) === "tool-result");
      if (!hasToolResult) {
        return true;
      }

      return !parts.every((part) => {
        if (coerceString(part.type) !== "tool-result") {
          return false;
        }

        const toolCallId = coerceString(part.toolCallId);
        return Boolean(toolCallId && mergeableToolCallIds.has(toolCallId));
      });
    })
    .map((message, index) => toThreadMessage(message, `persisted-${index}`, toolResultsByCallId))
    .filter((message) => !isEmptyAssistantMessage(message));

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
    messages: attachCanonicalMetadata(
      dedupeAdjacentAssistantTextMessages(
        [...persistedMessages, ...optimisticMessages].filter((message) => !isEmptyAssistantMessage(message)),
      ),
      state,
    ),
    isRunning: transportIsRunning,
  };
}
