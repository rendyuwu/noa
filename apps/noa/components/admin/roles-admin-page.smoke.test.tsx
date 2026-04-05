import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  ApiError: class ApiError extends Error {},
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/components/lib/http/fetch-client", () => ({
  ApiError: mocks.ApiError,
  fetchWithAuth: (...args: unknown[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: unknown[]) => mocks.jsonOrThrow(...args),
}));

vi.mock("sonner", () => ({
  toast: mocks.toast,
}));

import { RolesAdminPage } from "./roles-admin-page";

describe("RolesAdminPage smoke", () => {
  beforeEach(() => {
    let callIndex = 0;
    const payloads = [
      { roles: ["admin", "editor"] },
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

    await screen.findByRole("button", { name: "Save allowlist" });
    await screen.findByRole("heading", { name: "Role catalog" });
    await screen.findByRole("heading", { name: "Access policy" });
    await screen.findByText("Toggle the tools this role can access. Save the allowlist to apply changes.");
    await screen.findByRole("button", { name: "threads:write" });
    fireEvent.click(screen.getByRole("button", { name: "threads:write" }));
    fireEvent.click(screen.getByRole("button", { name: "Save allowlist" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        "/admin/roles/admin/tools",
        expect.objectContaining({ method: "PUT" }),
      );
    });

    await waitFor(() => {
      expect(mocks.toast.success).toHaveBeenCalledWith("Tool allowlist updated.");
    });

    fireEvent.change(screen.getByRole("textbox", { name: "Search roles" }), {
      target: { value: "editor" },
    });
    await screen.findByRole("heading", { name: "editor" });

    fireEvent.change(screen.getByRole("textbox", { name: "Search roles" }), {
      target: { value: "zzz" },
    });
    await screen.findByText("Select a role to manage its tool allowlist and migration helpers.");
  });
});
