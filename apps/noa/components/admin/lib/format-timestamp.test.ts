import { describe, expect, it } from "vitest";

import { formatTimestamp } from "./format-timestamp";

describe("formatTimestamp", () => {
  it("returns a normalized UTC timestamp string", () => {
    expect(formatTimestamp("2026-04-04T10:11:12.123Z")).toBe("2026-04-04 10:11:12Z");
  });

  it("returns a dash for invalid values", () => {
    expect(formatTimestamp("not-a-date")).toBe("-");
    expect(formatTimestamp(null)).toBe("-");
  });
});
