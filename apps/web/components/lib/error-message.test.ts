import { describe, expect, it } from "vitest";

import { ApiError } from "./fetch-helper";

const loadToUserMessage = async () => {
  const modulePath = "./error-message";
  const module = await import(/* @vite-ignore */ modulePath);
  return module.toUserMessage;
};

describe("toUserMessage", () => {
  it("maps user_pending_approval to friendly copy", async () => {
    const toUserMessage = await loadToUserMessage();

    expect(
      toUserMessage({
        detail: "User pending approval",
        errorCode: "user_pending_approval",
      }),
    ).toBe("Your account is pending approval. Ask an admin to enable it.");
  });

  it("falls back safely for unknown network failures", async () => {
    const toUserMessage = await loadToUserMessage();

    expect(toUserMessage(new TypeError("Failed to fetch"))).toBe("Unable to reach API");
  });

  it("uses a safe fallback when ApiError detail is not meaningful", async () => {
    const toUserMessage = await loadToUserMessage();

    expect(toUserMessage(new ApiError(500, ""))).toBe("Unable to reach API");
  });
});
