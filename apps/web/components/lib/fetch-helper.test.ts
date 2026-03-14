import { describe, expect, it } from "vitest";

import { ApiError, jsonOrThrow } from "./fetch-helper";

describe("jsonOrThrow", () => {
  it("throws an ApiError with typed API details from snake_case payload fields", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "User pending approval",
        error_code: "user_pending_approval",
        request_id: "req-123",
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

  it("falls back to x-request-id header when request_id is missing from the payload", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "Forbidden",
      }),
      {
        status: 403,
        headers: {
          "content-type": "application/json",
          "x-request-id": "req-header-456",
        },
      },
    );

    await expect(jsonOrThrow(response)).rejects.toMatchObject({
      status: 403,
      detail: "Forbidden",
      requestId: "req-header-456",
    });
  });
});
