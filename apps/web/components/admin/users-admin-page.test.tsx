import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
}));

vi.mock("@/components/lib/fetch-helper", () => ({
  fetchWithAuth: (...args: any[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: any[]) => mocks.jsonOrThrow(...args),
}));

import { UsersAdminPage } from "./users-admin-page";

describe("UsersAdminPage", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
  });

  it("renders a Users heading, a table, and a user row after load", async () => {
    const payload = {
      users: [
        {
          id: "user-1",
          email: "casey@example.com",
          display_name: "Casey Rivers",
          roles: ["member"],
        },
      ],
    };

    const response = new Response(JSON.stringify(payload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    mocks.fetchWithAuth.mockResolvedValue(response);
    mocks.jsonOrThrow.mockResolvedValue(payload);

    render(<UsersAdminPage />);

    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();

    const table = await screen.findByRole("table");
    expect(table).toBeInTheDocument();
    expect(within(table).getByText("casey@example.com")).toBeInTheDocument();
  });
});
