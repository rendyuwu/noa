import { describe, expect, it } from "vitest";

import { formatTimestampLocalized } from "./admin-data";

describe("formatTimestampLocalized", () => {
  it("returns a localized timestamp for valid values", () => {
    expect(formatTimestampLocalized("2026-04-04T10:11:12.123Z")).not.toBe("—");
  });

  it("returns a dash for invalid values", () => {
    expect(formatTimestampLocalized("not-a-date")).toBe("—");
    expect(formatTimestampLocalized(null)).toBe("—");
  });
});
