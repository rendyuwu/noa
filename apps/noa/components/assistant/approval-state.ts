export type AssistantPendingApproval = {
  actionRequestId: string;
  toolName: string;
  risk: string;
  arguments: Record<string, unknown>;
  status: string;
};

export type AssistantActionLifecycleStatus =
  | "requested"
  | "approved"
  | "executing"
  | "finished"
  | "failed"
  | "denied";

export type AssistantActionRequest = {
  actionRequestId: string;
  toolName: string;
  risk: string;
  arguments: Record<string, unknown>;
  status: string;
  lifecycleStatus: AssistantActionLifecycleStatus;
};

export type AssistantDetailEvidenceItem = {
  label: string;
  value: string;
};

export type AssistantDetailEvidenceSection = {
  title: string;
  items: AssistantDetailEvidenceItem[];
};

function coerceString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function coerceRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function coerceLifecycleStatus(value: unknown): AssistantActionLifecycleStatus | undefined {
  return value === "requested" ||
    value === "approved" ||
    value === "executing" ||
    value === "finished" ||
    value === "failed" ||
    value === "denied"
    ? value
    : undefined;
}

function coerceDetailEvidenceItem(value: unknown): AssistantDetailEvidenceItem | undefined {
  const record = coerceRecord(value);
  const label = coerceString(record?.label);
  const rawValue = record?.value;
  if (!label) {
    return undefined;
  }

  if (typeof rawValue === "string") {
    return { label, value: rawValue };
  }

  if (typeof rawValue === "number" || typeof rawValue === "boolean") {
    return { label, value: String(rawValue) };
  }

  return undefined;
}

export function coerceDetailEvidenceSections(value: unknown): AssistantDetailEvidenceSection[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }

  return value.flatMap((entry) => {
    const record = coerceRecord(entry);
    const title = coerceString(record?.title);
    const items = Array.isArray(record?.items)
      ? record.items
          .map((item) => coerceDetailEvidenceItem(item))
          .filter((item): item is AssistantDetailEvidenceItem => Boolean(item))
      : [];

    if (!title || items.length === 0) {
      return [];
    }

    return [{ title, items }];
  });
}

function isPendingApproval(value: unknown): value is AssistantPendingApproval {
  const record = coerceRecord(value);

  return Boolean(
    record &&
      coerceString(record.actionRequestId) &&
      coerceString(record.toolName) &&
      coerceString(record.risk) &&
      coerceRecord(record.arguments) &&
      coerceString(record.status),
  );
}

function isActionRequest(value: unknown): value is AssistantActionRequest {
  const record = coerceRecord(value);

  return Boolean(
    record &&
      coerceString(record.actionRequestId) &&
      coerceString(record.toolName) &&
      coerceString(record.risk) &&
      coerceRecord(record.arguments) &&
      coerceString(record.status) &&
      coerceLifecycleStatus(record.lifecycleStatus),
  );
}

export function coercePendingApprovals(value: unknown): AssistantPendingApproval[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }

  return value.filter(isPendingApproval);
}

export function coerceActionRequests(value: unknown): AssistantActionRequest[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }

  return value.filter(isActionRequest);
}

export function extractLatestCanonicalActionRequests(messages: unknown): AssistantActionRequest[] | undefined {
  if (!Array.isArray(messages)) {
    return undefined;
  }

  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = coerceRecord(messages[messageIndex]);
    const metadata = coerceRecord(message?.metadata);
    const custom = coerceRecord(metadata?.custom);

    if (!custom) {
      continue;
    }

    const actionRequests = coerceActionRequests(custom.actionRequests);
    if (actionRequests) {
      return actionRequests;
    }

    const pendingApprovals = coercePendingApprovals(custom.pendingApprovals);
    if (pendingApprovals) {
      return pendingApprovals.map((approval) => ({
        ...approval,
        lifecycleStatus: "requested",
      }));
    }
  }

  return undefined;
}

export function extractLatestCanonicalEvidenceSections(
  messages: unknown,
): AssistantDetailEvidenceSection[] | undefined {
  if (!Array.isArray(messages)) {
    return undefined;
  }

  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = coerceRecord(messages[messageIndex]);
    const metadata = coerceRecord(message?.metadata);
    const custom = coerceRecord(metadata?.custom);

    if (!custom) {
      continue;
    }

    const evidenceSections = coerceDetailEvidenceSections(custom.evidenceSections);
    if (evidenceSections) {
      return evidenceSections;
    }
  }

  return undefined;
}
