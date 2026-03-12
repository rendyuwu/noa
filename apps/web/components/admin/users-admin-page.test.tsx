import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
}));

vi.mock("@/components/lib/fetch-helper", () => ({
  getApiUrl: () => "/api",
  ApiError: class ApiError extends Error {
    status: number;

    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
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
    const usersPayload = {
      users: [
        {
          id: "a6c6e5b2-5d50-4c1e-92c1-9a06b0a2c9fb",
          email: "casey@example.com",
          display_name: "Casey Rivers",
          is_active: true,
          roles: ["member"],
          tools: ["get_current_time", "get_current_date"],
        },
      ],
    };

    const toolsPayload = {
      tools: ["get_current_time", "get_current_date", "set_demo_flag"],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const toolsResponse = new Response(JSON.stringify(toolsPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string) => {
      if (path === "/admin/users") {
        return usersResponse;
      }
      if (path === "/admin/tools") {
        return toolsResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === usersResponse) {
        return usersPayload;
      }
      if (response === toolsResponse) {
        return toolsPayload;
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(table).toBeInTheDocument();

    await waitFor(() => {
      const calledPaths = mocks.fetchWithAuth.mock.calls.map((call) => call[0]);
      expect(calledPaths).toEqual(expect.arrayContaining(["/admin/users", "/admin/tools"]));
    });

    expect(await within(table).findByText("casey@example.com")).toBeInTheDocument();
  });

  it("PATCHes /admin/users/:id to disable an active user from the drawer", async () => {
    const userId = "a6c6e5b2-5d50-4c1e-92c1-9a06b0a2c9fb";
    const usersPayload = {
      users: [
        {
          id: userId,
          email: "casey@example.com",
          display_name: "Casey Rivers",
          is_active: true,
          roles: ["member"],
          tools: ["get_current_time"],
        },
      ],
    };

    const toolsPayload = {
      tools: ["get_current_time"],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const toolsResponse = new Response(JSON.stringify(toolsPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const patchResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string) => {
      if (path === "/admin/users") {
        return usersResponse;
      }
      if (path === "/admin/tools") {
        return toolsResponse;
      }
      if (path === `/admin/users/${userId}`) {
        return patchResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === usersResponse) {
        return usersPayload;
      }
      if (response === toolsResponse) {
        return toolsPayload;
      }
      if (response === patchResponse) {
        return { ok: true };
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    const table = screen.getByRole("table");
    const emailCell = await within(table).findByText("casey@example.com");
    const row = emailCell.closest("tr");
    if (!row) throw new Error("Unable to locate user row");
    fireEvent.click(row);

    const disableButton = await screen.findByRole("button", { name: "Disable" });
    fireEvent.click(disableButton);

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(`/admin/users/${userId}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ is_active: false }),
      });
    });
  });

  it("shows an inline error banner when status update returns a conflict", async () => {
    const userId = "a6c6e5b2-5d50-4c1e-92c1-9a06b0a2c9fb";
    const conflictMessage = "Cannot disable the last active admin";
    const usersPayload = {
      users: [
        {
          id: userId,
          email: "casey@example.com",
          display_name: "Casey Rivers",
          is_active: true,
          roles: ["admin"],
          tools: ["get_current_time"],
        },
      ],
    };

    const toolsPayload = {
      tools: ["get_current_time"],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const toolsResponse = new Response(JSON.stringify(toolsPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const patchResponse = new Response(JSON.stringify({ detail: conflictMessage }), {
      status: 409,
      headers: {
        "content-type": "application/json",
      },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string) => {
      if (path === "/admin/users") {
        return usersResponse;
      }
      if (path === "/admin/tools") {
        return toolsResponse;
      }
      if (path === `/admin/users/${userId}`) {
        return patchResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === usersResponse) {
        return usersPayload;
      }
      if (response === toolsResponse) {
        return toolsPayload;
      }
      if (response === patchResponse) {
        throw new Error(conflictMessage);
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    const table = screen.getByRole("table");
    const emailCell = await within(table).findByText("casey@example.com");
    const row = emailCell.closest("tr");
    if (!row) throw new Error("Unable to locate user row");
    fireEvent.click(row);

    const disableButton = await screen.findByRole("button", { name: "Disable" });
    fireEvent.click(disableButton);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(conflictMessage);
  });
});
