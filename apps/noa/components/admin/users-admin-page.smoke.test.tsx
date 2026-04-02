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
        ],
      },
      { roles: ["viewer", "admin"] },
      {
        user: {
          id: "user-1",
          email: "admin@example.com",
          display_name: "Admin User",
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
    fireEvent.click(screen.getByRole("button", { name: "admin" }));
    fireEvent.click(screen.getByRole("button", { name: "Save roles" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        "/admin/users/user-1/roles",
        expect.objectContaining({ method: "PUT" }),
      );
    });

    await screen.findByText("Roles updated.");
  });
});
