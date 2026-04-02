import type { DirectGrantsMigrationResponse } from "./types";

function coerceFiniteNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pickCount(payload: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = coerceFiniteNumber(payload[key]);
    if (value !== null) {
      return value;
    }
  }

  return null;
}

export function formatMigrationSummary(payload: DirectGrantsMigrationResponse) {
  const usersMigrated = pickCount(payload, ["users_migrated", "usersMigrated"]);
  const rolesCreated = pickCount(payload, ["roles_created", "rolesCreated"]);
  const rolesReused = pickCount(payload, ["roles_reused", "rolesReused"]);

  const parts: string[] = [];
  if (usersMigrated !== null) {
    parts.push(`${usersMigrated} user${usersMigrated === 1 ? "" : "s"} migrated`);
  }
  if (rolesCreated !== null) {
    parts.push(`${rolesCreated} role${rolesCreated === 1 ? "" : "s"} created`);
  }
  if (rolesReused !== null) {
    parts.push(`${rolesReused} role${rolesReused === 1 ? "" : "s"} reused`);
  }

  return parts.length > 0 ? `Migration complete: ${parts.join("; ")}.` : "Migration completed.";
}
