import { describe, expect, it } from "vitest";

import { formatMigrationSummary } from "./format-migration-summary";

describe("formatMigrationSummary", () => {
  it("builds a summary when migration counters are present", () => {
    expect(
      formatMigrationSummary({
        roles_created: 2,
        roles_reused: 1,
        users_migrated: 5,
      }),
    ).toBe("Migration complete: 5 users migrated; 2 roles created; 1 role reused.");
  });

  it("falls back to a generic message when counters are absent", () => {
    expect(formatMigrationSummary({ ok: true })).toBe("Migration completed.");
  });
});
