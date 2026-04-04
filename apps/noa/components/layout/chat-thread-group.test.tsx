import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { groupThreadsByDate, type GroupableThread } from "./chat-thread-group";

describe("groupThreadsByDate", () => {
  const NOW = new Date("2026-04-04T12:00:00Z");

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function thread(id: string, updatedAt: string): GroupableThread {
    return { id, updatedAt };
  }

  it("places a thread updated today in the 'Today' group", () => {
    const threads = [thread("a", "2026-04-04T08:00:00Z")];
    const groups = groupThreadsByDate(threads);
    expect(groups[0]?.label).toBe("Today");
    expect(groups[0]?.threads).toHaveLength(1);
  });

  it("places a thread updated yesterday in the 'Yesterday' group", () => {
    const threads = [thread("a", "2026-04-03T20:00:00Z")];
    const groups = groupThreadsByDate(threads);
    expect(groups[0]?.label).toBe("Yesterday");
  });

  it("places a thread updated 5 days ago in 'Previous 7 days'", () => {
    const threads = [thread("a", "2026-03-30T12:00:00Z")];
    const groups = groupThreadsByDate(threads);
    expect(groups[0]?.label).toBe("Previous 7 days");
  });

  it("places a thread updated 20 days ago in 'Previous 30 days'", () => {
    const threads = [thread("a", "2026-03-15T12:00:00Z")];
    const groups = groupThreadsByDate(threads);
    expect(groups[0]?.label).toBe("Previous 30 days");
  });

  it("places a thread updated 60 days ago in 'Older'", () => {
    const threads = [thread("a", "2026-02-03T12:00:00Z")];
    const groups = groupThreadsByDate(threads);
    expect(groups[0]?.label).toBe("Older");
  });

  it("returns groups in chronological order (newest first)", () => {
    const threads = [
      thread("old", "2026-01-01T00:00:00Z"),
      thread("today", "2026-04-04T08:00:00Z"),
      thread("yesterday", "2026-04-03T18:00:00Z"),
    ];
    const groups = groupThreadsByDate(threads);
    const labels = groups.map((g) => g.label);
    expect(labels).toEqual(["Today", "Yesterday", "Older"]);
  });

  it("returns empty array for empty input", () => {
    expect(groupThreadsByDate([])).toEqual([]);
  });

  it("treats threads without updatedAt as 'Older'", () => {
    const threads = [{ id: "x", updatedAt: undefined as unknown as string }];
    const groups = groupThreadsByDate(threads);
    expect(groups[0]?.label).toBe("Older");
  });
});
