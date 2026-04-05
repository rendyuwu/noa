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

import { UsersAdminPage } from "./users-admin-page";

describe("UsersAdminPage smoke", () => {
  beforeEach(() => {
    let callIndex = 0;
    const payloads = [
      {
        users: [
          {
            id: "user-1",
            email: "admin@example.com",
            display_name: "Admin User",
            is_active: true,
            roles: ["viewer"],
            direct_tools: [],
          },
          {
            id: "user-2",
            email: "bob@example.com",
            display_name: "Bob Person",
            is_active: true,
            roles: ["viewer"],
            direct_tools: [],
          },
        ],
      },
      { roles: ["viewer", "admin"] },
      {
        user: {
          id: "user-2",
          email: "bob@example.com",
          display_name: "Bob Person",
          is_active: true,
          roles: ["admin", "viewer"],
          direct_tools: [],
        },
      },
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

  it("loads users and saves updated role assignments", async () => {
    render(<UsersAdminPage />);

    await screen.findByRole("button", { name: "Save roles" });
    await screen.findByRole("heading", { name: "Admin User · Active" });
    await screen.findByRole("heading", { name: "Account overview" });
    await screen.findByRole("heading", { name: "Danger zone" });
    expect(screen.getAllByRole("heading", { name: "Access control" })).toHaveLength(1);
    fireEvent.change(screen.getByRole("textbox", { name: "Search users" }), {
      target: { value: "bob" },
    });
    await screen.findByRole("heading", { name: "Bob Person · Active" });
    fireEvent.click(screen.getByRole("button", { name: "admin" }));
    fireEvent.click(screen.getByRole("button", { name: "Save roles" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        "/admin/users/user-2/roles",
        expect.objectContaining({ method: "PUT" }),
      );
    });

    await waitFor(() => {
      expect(mocks.toast.success).toHaveBeenCalledWith("Roles updated.");
    });

    fireEvent.change(screen.getByRole("textbox", { name: "Search users" }), {
      target: { value: "zzz" },
    });
    await screen.findByText("Select a user to inspect role assignments and account status.");
  });
});
