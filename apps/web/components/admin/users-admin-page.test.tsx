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
    detail: string;

    constructor(status: number, message: string) {
      super(message);
      this.status = status;
      this.detail = message;
    }
  },
  fetchWithAuth: (...args: any[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: any[]) => mocks.jsonOrThrow(...args),
}));

import { ApiError } from "@/components/lib/fetch-helper";
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
          created_at: "2026-01-02T03:04:05.000Z",
          last_login_at: "2026-02-03T04:05:06.000Z",
          is_active: true,
          roles: ["member"],
          tools: ["get_current_time", "get_current_date"],
        },
      ],
    };

    const rolesPayload = {
      roles: [{ name: "member" }, { name: "admin" }],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string) => {
      if (path === "/admin/users") {
        return usersResponse;
      }
      if (path === "/admin/roles") {
        return rolesResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === usersResponse) {
        return usersPayload;
      }
      if (response === rolesResponse) {
        return rolesPayload;
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
    const table = await screen.findByRole("table");
    expect(table).toBeInTheDocument();

    await waitFor(() => {
      const calledPaths = mocks.fetchWithAuth.mock.calls.map((call) => call[0]);
      expect(calledPaths).toEqual(expect.arrayContaining(["/admin/users", "/admin/roles"]));
    });

    expect(await within(table).findByText("casey@example.com")).toBeInTheDocument();
  });

  it("renders Created and Last login columns with pending and disabled status labels", async () => {
    const usersPayload = {
      users: [
        {
          id: "5abf1606-aad2-4935-8b9c-fe017a8b704e",
          email: "pending@example.com",
          display_name: "Pending User",
          created_at: "2026-01-02T03:04:05.000Z",
          last_login_at: null,
          is_active: false,
          roles: ["member"],
          tools: [],
        },
        {
          id: "577f5df4-ab12-42e7-b4ca-e4c7c15ca6c1",
          email: "disabled@example.com",
          display_name: "Disabled User",
          created_at: "2026-01-03T03:04:05.000Z",
          last_login_at: "2026-01-10T09:08:07.000Z",
          is_active: false,
          roles: ["member"],
          tools: [],
        },
      ],
    };

    const rolesPayload = {
      roles: [{ name: "member" }, { name: "admin" }],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string) => {
      if (path === "/admin/users") {
        return usersResponse;
      }
      if (path === "/admin/roles") {
        return rolesResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === usersResponse) {
        return usersPayload;
      }
      if (response === rolesResponse) {
        return rolesPayload;
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    const table = await screen.findByRole("table");

    await waitFor(() => {
      const calledPaths = mocks.fetchWithAuth.mock.calls.map((call) => call[0]);
      expect(calledPaths).toEqual(expect.arrayContaining(["/admin/users", "/admin/roles"]));
    });

    expect(within(table).getByRole("columnheader", { name: "Created" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Last login" })).toBeInTheDocument();

    expect(await within(table).findByText("pending@example.com")).toBeInTheDocument();
    expect(within(table).getByText("Pending")).toBeInTheDocument();
    expect(within(table).getByText("disabled@example.com")).toBeInTheDocument();
    expect(within(table).getByText("Disabled")).toBeInTheDocument();
  });

  it("shows contextual fallback copy instead of raw load errors", async () => {
    mocks.fetchWithAuth.mockImplementation(async (path: string) => {
      if (path === "/admin/users" || path === "/admin/roles") {
        throw new Error("database connection exploded");
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    render(<UsersAdminPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Unable to load users");
    expect(alert).not.toHaveTextContent("database connection exploded");
  });

  it("PUTs /admin/users/:id/roles to update role assignments from the modal", async () => {
    const userId = "a6c6e5b2-5d50-4c1e-92c1-9a06b0a2c9fb";
    const usersPayload = {
      users: [
        {
          id: userId,
          email: "casey@example.com",
          display_name: "Casey Rivers",
          created_at: "2026-01-02T03:04:05.000Z",
          last_login_at: "2026-02-03T04:05:06.000Z",
          is_active: true,
          roles: ["member"],
          tools: ["get_current_time"],
          direct_tools: ["legacy_tool"],
        },
      ],
    };

    const rolesPayload = {
      roles: [{ name: "member" }, { name: "admin" }],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const putPayload = {
      user: {
        ...usersPayload.users[0],
        roles: ["admin", "member"],
      },
    };

    const putResponse = new Response(JSON.stringify(putPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/users") return usersResponse;
      if (path === "/admin/roles") return rolesResponse;
      if (path === `/admin/users/${userId}/roles` && init?.method === "PUT") return putResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === usersResponse) return usersPayload;
      if (response === rolesResponse) return rolesPayload;
      if (response === putResponse) return putPayload;
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    const table = await screen.findByRole("table");
    fireEvent.click(await within(table).findByRole("button", { name: /casey@example.com/i }));

    fireEvent.click(await screen.findByLabelText("admin"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        `/admin/users/${userId}/roles`,
        expect.objectContaining({
          method: "PUT",
          headers: expect.objectContaining({
            "content-type": "application/json",
          }),
          body: JSON.stringify({ roles: ["admin", "member"] }),
        })
      );
    });
  });

  it("shows legacy direct grants as read-only (no clear action)", async () => {
    const userId = "a6c6e5b2-5d50-4c1e-92c1-9a06b0a2c9fb";
    const usersPayload = {
      users: [
        {
          id: userId,
          email: "casey@example.com",
          display_name: "Casey Rivers",
          created_at: "2026-01-02T03:04:05.000Z",
          last_login_at: "2026-02-03T04:05:06.000Z",
          is_active: true,
          roles: ["member"],
          tools: ["get_current_time"],
          direct_tools: ["legacy_tool"],
        },
      ],
    };

    const rolesPayload = {
      roles: [{ name: "member" }, { name: "admin" }],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string) => {
      if (path === "/admin/users") return usersResponse;
      if (path === "/admin/roles") return rolesResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === usersResponse) return usersPayload;
      if (response === rolesResponse) return rolesPayload;
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    const table = await screen.findByRole("table");
    fireEvent.click(await within(table).findByRole("button", { name: /casey@example.com/i }));

    expect(await screen.findByText("Legacy direct grants")).toBeInTheDocument();
    expect(screen.getByText("legacy_tool")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /clear direct grants/i })).not.toBeInTheDocument();
  });

  it("PATCHes /admin/users/:id to disable an active user from the modal header", async () => {
    const userId = "a6c6e5b2-5d50-4c1e-92c1-9a06b0a2c9fb";
    const usersPayload = {
      users: [
        {
          id: userId,
          email: "casey@example.com",
          display_name: "Casey Rivers",
          created_at: "2026-01-02T03:04:05.000Z",
          last_login_at: "2026-02-03T04:05:06.000Z",
          is_active: true,
          roles: ["member"],
          tools: ["get_current_time"],
        },
      ],
    };

    const rolesPayload = {
      roles: [{ name: "member" }, { name: "admin" }],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
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
      if (path === "/admin/roles") {
        return rolesResponse;
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
      if (response === rolesResponse) {
        return rolesPayload;
      }
      if (response === patchResponse) {
        return { ok: true };
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    const table = await screen.findByRole("table");
    fireEvent.click(await within(table).findByRole("button", { name: /casey@example.com/i }));

    const disableButton = await screen.findByRole("button", { name: "Disable" });
    fireEvent.click(disableButton);

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        `/admin/users/${userId}`,
        expect.objectContaining({
          method: "PATCH",
          headers: expect.objectContaining({
            "content-type": "application/json",
          }),
          body: JSON.stringify({ is_active: false }),
        })
      );
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
          created_at: "2026-01-02T03:04:05.000Z",
          last_login_at: "2026-02-03T04:05:06.000Z",
          is_active: true,
          roles: ["admin"],
          tools: ["get_current_time"],
        },
      ],
    };

    const rolesPayload = {
      roles: [{ name: "member" }, { name: "admin" }],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
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
      if (path === "/admin/roles") {
        return rolesResponse;
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
      if (response === rolesResponse) {
        return rolesPayload;
      }
      if (response === patchResponse) {
        throw new ApiError(409, conflictMessage);
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    const table = await screen.findByRole("table");
    fireEvent.click(await within(table).findByRole("button", { name: /casey@example.com/i }));

    const disableButton = await screen.findByRole("button", { name: "Disable" });
    fireEvent.click(disableButton);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(conflictMessage);
  });

  it("does not show a stale status error after switching users", async () => {
    const userAId = "a6c6e5b2-5d50-4c1e-92c1-9a06b0a2c9fb";
    const userBId = "0bdb2ba6-6ad5-4683-bce2-5f4a7b9d1e0e";
    const conflictMessage = "Cannot disable the last active admin";

    const usersPayload = {
      users: [
        {
          id: userAId,
          email: "casey@example.com",
          display_name: "Casey Rivers",
          created_at: "2026-01-02T03:04:05.000Z",
          last_login_at: "2026-02-03T04:05:06.000Z",
          is_active: true,
          roles: ["admin"],
          tools: ["get_current_time"],
        },
        {
          id: userBId,
          email: "riley@example.com",
          display_name: "Riley Smith",
          created_at: "2026-01-10T07:08:09.000Z",
          last_login_at: "2026-02-11T10:11:12.000Z",
          is_active: true,
          roles: ["admin"],
          tools: ["get_current_time"],
        },
      ],
    };

    const rolesPayload = {
      roles: [{ name: "admin" }],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
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

    let resolveUserAPatch: ((value: Response) => void) | null = null;
    const userAPatchPromise = new Promise<Response>((resolve) => {
      resolveUserAPatch = resolve;
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string) => {
      if (path === "/admin/users") {
        return usersResponse;
      }
      if (path === "/admin/roles") {
        return rolesResponse;
      }
      if (path === `/admin/users/${userAId}`) {
        return userAPatchPromise;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === usersResponse) {
        return usersPayload;
      }
      if (response === rolesResponse) {
        return rolesPayload;
      }
      if (response === patchResponse) {
        throw new ApiError(409, conflictMessage);
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    const table = await screen.findByRole("table");
    fireEvent.click(await within(table).findByRole("button", { name: /casey@example.com/i }));

    const disableButton = await screen.findByRole("button", { name: "Disable" });
    fireEvent.click(disableButton);

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(`/admin/users/${userAId}`, expect.anything());
    });

    const closeButton = await screen.findByRole("button", { name: "Close" });
    fireEvent.click(closeButton);

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    fireEvent.click(await within(table).findByRole("button", { name: /riley@example.com/i }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("riley@example.com")).toBeInTheDocument();

    if (!resolveUserAPatch) throw new Error("Missing patch promise resolver");
    resolveUserAPatch(patchResponse);

    await waitFor(() => {
      expect(mocks.jsonOrThrow).toHaveBeenCalledWith(patchResponse);
    });

    expect(within(screen.getByRole("dialog")).queryByText(conflictMessage)).not.toBeInTheDocument();
  });

  it("DELETEs /admin/users/:id from the modal danger zone", async () => {
    const userId = "a6c6e5b2-5d50-4c1e-92c1-9a06b0a2c9fb";
    const usersPayload = {
      users: [
        {
          id: userId,
          email: "casey@example.com",
          display_name: "Casey Rivers",
          created_at: "2026-01-02T03:04:05.000Z",
          last_login_at: "2026-02-03T04:05:06.000Z",
          is_active: true,
          roles: ["member"],
          tools: ["get_current_time"],
        },
      ],
    };

    const rolesPayload = {
      roles: [{ name: "member" }, { name: "admin" }],
    };

    const usersResponse = new Response(JSON.stringify(usersPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    const deleteResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: {
        "content-type": "application/json",
      },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/users") {
        return usersResponse;
      }
      if (path === "/admin/roles") {
        return rolesResponse;
      }
      if (path === `/admin/users/${userId}` && init?.method === "DELETE") {
        return deleteResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === usersResponse) {
        return usersPayload;
      }
      if (response === rolesResponse) {
        return rolesPayload;
      }
      if (response === deleteResponse) {
        return { ok: true };
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<UsersAdminPage />);

    const table = await screen.findByRole("table");
    fireEvent.click(await within(table).findByRole("button", { name: /casey@example.com/i }));

    fireEvent.click(await screen.findByRole("button", { name: "Delete user" }));

    const confirmDialog = await screen.findByRole("dialog", { name: "Delete user?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete user" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(`/admin/users/${userId}`, {
        method: "DELETE",
      });
    });

    await waitFor(() => {
      expect(within(table).queryByText("casey@example.com")).not.toBeInTheDocument();
    });
  });
});
