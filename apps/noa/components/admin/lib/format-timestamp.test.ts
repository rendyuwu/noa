import { describe, expect, it } from "vitest";

import { formatTimestampUTC } from "./format-timestamp";

describe("formatTimestampUTC", () => {
  it("returns a normalized UTC timestamp string", () => {
    expect(formatTimestampUTC("2026-04-04T10:11:12.123Z")).toBe("2026-04-04 10:11:12Z");
  });

  it("returns a dash for invalid values", () => {
    expect(formatTimestampUTC("not-a-date")).toBe("-");
    expect(formatTimestampUTC(null)).toBe("-");
  });
});
