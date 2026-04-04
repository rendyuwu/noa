export function coerceStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
}

export function coerceRoleNames(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const direct = value.filter((entry): entry is string => typeof entry === "string");
  if (direct.length > 0) {
    return direct;
  }

  return value.flatMap((entry) => {
    if (entry && typeof entry === "object" && "name" in entry && typeof entry.name === "string") {
      return [entry.name];
    }

    return [];
  });
}

export function formatTimestampLocalized(value: unknown) {
  if (typeof value !== "string" || !value) {
    return "—";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
