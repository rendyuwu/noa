import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
}));

vi.mock("@/components/lib/http/fetch-client", () => ({
  fetchWithAuth: (...args: unknown[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: unknown[]) => mocks.jsonOrThrow(...args),
}));

import { RolesAdminPage } from "./roles-admin-page";

describe("RolesAdminPage smoke", () => {
  beforeEach(() => {
    let callIndex = 0;
    const payloads = [
      { roles: ["admin"] },
      { tools: ["threads:read", "threads:write"] },
      { tools: ["threads:read"] },
      { tools: ["threads:read", "threads:write"] },
    ];

    mocks.fetchWithAuth.mockReset();
    mocks.fetchWithAuth.mockResolvedValue(new Response(null, { status: 200 }));
    mocks.jsonOrThrow.mockReset();
    mocks.jsonOrThrow.mockImplementation(async () => {
      const payload = payloads[callIndex];
      callIndex += 1;
      return payload;
    });
  });

  it("loads role allowlists and saves updated tools", async () => {
    render(<RolesAdminPage />);

    await screen.findByRole("button", { name: "threads:write" });
    fireEvent.click(screen.getByRole("button", { name: "threads:write" }));
    fireEvent.click(screen.getByRole("button", { name: "Save allowlist" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        "/admin/roles/admin/tools",
        expect.objectContaining({ method: "PUT" }),
      );
    });

    await screen.findByText("Tool allowlist updated.");
  });
});
