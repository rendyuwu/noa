import { describe, expect, it } from "vitest";

import {
  formatClaudeGreetingName,
  getClaudeTimeGreeting,
} from "./claude-greeting";

describe("formatClaudeGreetingName", () => {
  it("prefers a trimmed display name", () => {
    expect(
      formatClaudeGreetingName({
        id: "1",
        email: "person@example.com",
        display_name: "  Casey  ",
      }),
    ).toBe("Casey");
  });

  it("falls back to a humanized email local part", () => {
    expect(
      formatClaudeGreetingName({
        id: "1",
        email: "person@example.com",
        display_name: "   ",
      }),
    ).toBe("Person");
  });

  it("splits dotted email local parts into words", () => {
    expect(
      formatClaudeGreetingName({
        id: "1",
        email: "smoke.example@noa.test",
        display_name: "   ",
      }),
    ).toBe("Smoke Example");
  });

  it("falls back to a generic greeting when user data is missing", () => {
    expect(formatClaudeGreetingName(null)).toBe("there");
  });
});

describe("getClaudeTimeGreeting", () => {
  it("returns Morning before noon", () => {
    expect(getClaudeTimeGreeting(new Date(2026, 2, 10, 11, 59))).toBe("Morning");
  });

  it("returns Afternoon from noon until evening", () => {
    expect(getClaudeTimeGreeting(new Date(2026, 2, 10, 12, 0))).toBe("Afternoon");
    expect(getClaudeTimeGreeting(new Date(2026, 2, 10, 17, 59))).toBe("Afternoon");
  });

  it("returns Evening at 6pm and later", () => {
    expect(getClaudeTimeGreeting(new Date(2026, 2, 10, 18, 0))).toBe("Evening");
  });
});
