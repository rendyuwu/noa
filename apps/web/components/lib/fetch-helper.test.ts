import { describe, expect, it } from "vitest";

import { ApiError, jsonOrThrow } from "./fetch-helper";

describe("jsonOrThrow", () => {
  it("throws an ApiError with typed API details", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "User pending approval",
        errorCode: "user_pending_approval",
        requestId: "req-123",
      }),
      {
        status: 403,
        headers: {
          "content-type": "application/json",
        },
      },
    );

    let thrown: unknown;

    try {
      await jsonOrThrow(response);
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(ApiError);
    expect(thrown).toMatchObject({
      status: 403,
      detail: "User pending approval",
      errorCode: "user_pending_approval",
      requestId: "req-123",
    });
  });
});
