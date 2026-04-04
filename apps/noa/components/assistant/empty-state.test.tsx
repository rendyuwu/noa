import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { getGreeting } from "./empty-state";

describe("getGreeting", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns morning greeting between 5-12", () => {
    vi.setSystemTime(new Date("2026-04-04T08:00:00"));
    expect(getGreeting()).toBe("Good morning");
  });

  it("returns afternoon greeting between 12-17", () => {
    vi.setSystemTime(new Date("2026-04-04T14:00:00"));
    expect(getGreeting()).toBe("Good afternoon");
  });

  it("returns evening greeting between 17-21", () => {
    vi.setSystemTime(new Date("2026-04-04T19:00:00"));
    expect(getGreeting()).toBe("Good evening");
  });

  it("returns night greeting between 21-5", () => {
    vi.setSystemTime(new Date("2026-04-04T23:00:00"));
    expect(getGreeting()).toBe("Hello, night owl");
  });

  it("returns night greeting at 3am", () => {
    vi.setSystemTime(new Date("2026-04-04T03:00:00"));
    expect(getGreeting()).toBe("Hello, night owl");
  });
});
