import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();
const jsonOrThrow = vi.fn();

vi.mock("@/components/lib/http/fetch-client", () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuth(...args),
  jsonOrThrow: (...args: unknown[]) => jsonOrThrow(...args),
}));

import { threadListAdapter } from "./thread-list-adapter";

describe("threadListAdapter", () => {
  beforeEach(() => {
    fetchWithAuth.mockReset();
    jsonOrThrow.mockReset();
  });

  it("lists threads through the shared auth-aware HTTP client", async () => {
    const response = new Response(null, { status: 200 });
    fetchWithAuth.mockResolvedValue(response);
    jsonOrThrow.mockResolvedValue({
      threads: [
        { id: "1", remoteId: "remote-1", externalId: "local-1", status: "regular", title: "First" },
      ],
    });

    await expect(threadListAdapter.list()).resolves.toEqual({
      threads: [
        {
          remoteId: "remote-1",
          externalId: "local-1",
          status: "regular",
          title: "First",
        },
      ],
    });

    expect(fetchWithAuth).toHaveBeenCalledWith("/threads");
  });

  it("initializes threads with the provided local id", async () => {
    const response = new Response(null, { status: 200 });
    fetchWithAuth.mockResolvedValue(response);
    jsonOrThrow.mockResolvedValue({
      id: "1",
      remoteId: "remote-1",
      externalId: "draft-1",
      status: "regular",
      title: null,
    });

    await expect(threadListAdapter.initialize("draft-1")).resolves.toEqual({
      remoteId: "remote-1",
      externalId: "draft-1",
    });

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/threads",
      expect.objectContaining({
        method: "POST",
      }),
    );
  });

  it("treats delete 404s as already gone", async () => {
    fetchWithAuth.mockResolvedValue(new Response(null, { status: 404 }));

    await expect(threadListAdapter.delete("missing-thread")).resolves.toBeUndefined();
  });
});
