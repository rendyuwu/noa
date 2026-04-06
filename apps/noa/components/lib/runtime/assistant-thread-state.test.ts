import { describe, expect, it } from "vitest";

import { getActiveThreadListItem } from "./assistant-thread-state";

describe("getActiveThreadListItem", () => {
  it("falls back to remote and external ids when the main thread id is unavailable", () => {
    expect(
      getActiveThreadListItem({
        mainThreadId: "remote-thread",
        threadItems: [
          { id: "local-thread", remoteId: "remote-thread", externalId: "external-thread", status: "regular" },
        ],
      }),
    ).toEqual({
      id: "local-thread",
      remoteId: "remote-thread",
      externalId: "external-thread",
      status: "regular",
    });
  });
});
