import { describe, expect, it } from "vitest";

import { buildReturnTo, sanitizeReturnTo } from "./return-to";

describe("sanitizeReturnTo", () => {
  it("falls back to /assistant for empty values", () => {
    expect(sanitizeReturnTo(undefined)).toBe("/assistant");
    expect(sanitizeReturnTo("")).toBe("/assistant");
  });

  it("rejects external-looking targets", () => {
    expect(sanitizeReturnTo("https://evil.example")).toBe("/assistant");
    expect(sanitizeReturnTo("//evil.example")).toBe("/assistant");
  });

  it("preserves same-origin paths", () => {
    expect(sanitizeReturnTo("/admin/users?filter=active")).toBe(
      "/admin/users?filter=active",
    );
  });
});

describe("buildReturnTo", () => {
  it("combines pathname, search, and hash safely", () => {
    expect(buildReturnTo("/assistant", "?tab=threads", "#composer")).toBe(
      "/assistant?tab=threads#composer",
    );
  });
});
