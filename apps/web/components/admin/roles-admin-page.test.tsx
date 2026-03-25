import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
}));

vi.mock("@/components/lib/fetch-helper", () => ({
  fetchWithAuth: (...args: any[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: any[]) => mocks.jsonOrThrow(...args),
}));

import { RolesAdminPage } from "./roles-admin-page";

describe("RolesAdminPage", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
  });

  it("loads roles/tools and PUTs /admin/roles/:name/tools from the drawer", async () => {
    const rolesPayload = {
      roles: [{ name: "admin" }, { name: "member" }],
    };
    const toolsPayload = {
      tools: ["get_current_time", "set_demo_flag"],
    };
    const roleToolsPayload = {
      tools: ["get_current_time"],
    };

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const toolsResponse = new Response(JSON.stringify(toolsPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const roleToolsResponse = new Response(JSON.stringify(roleToolsPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const putResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/roles") return rolesResponse;
      if (path === "/admin/tools") return toolsResponse;
      if (path === "/admin/roles/admin/tools" && !init) return roleToolsResponse;
      if (path === "/admin/roles/admin/tools" && init?.method === "PUT") return putResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === rolesResponse) return rolesPayload;
      if (response === toolsResponse) return toolsPayload;
      if (response === roleToolsResponse) return roleToolsPayload;
      if (response === putResponse) return { ok: true };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<RolesAdminPage />);

    const table = screen.getByRole("table");
    const row = (await within(table).findByRole("row", { name: /manage admin/i })).closest("tr");
    if (!row) throw new Error("Missing role row");

    fireEvent.click(row);

    fireEvent.click(await screen.findByLabelText("set_demo_flag"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        "/admin/roles/admin/tools",
        expect.objectContaining({
          method: "PUT",
          headers: expect.objectContaining({
            "content-type": "application/json",
          }),
          body: JSON.stringify({ tools: ["get_current_time", "set_demo_flag"] }),
        })
      );
    });
  });

  it("creates and deletes roles via POST/DELETE /admin/roles", async () => {
    const rolesPayload = {
      roles: [{ name: "admin" }],
    };
    const toolsPayload = {
      tools: ["get_current_time"],
    };
    const emptyRoleToolsPayload = {
      tools: [],
    };

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const toolsResponse = new Response(JSON.stringify(toolsPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const postResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const roleToolsResponse = new Response(JSON.stringify(emptyRoleToolsPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const deleteResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/roles" && !init) return rolesResponse;
      if (path === "/admin/tools") return toolsResponse;
      if (path === "/admin/roles" && init?.method === "POST") return postResponse;
      if (path === "/admin/roles/support/tools" && !init) return roleToolsResponse;
      if (path === "/admin/roles/support" && init?.method === "DELETE") return deleteResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === rolesResponse) return rolesPayload;
      if (response === toolsResponse) return toolsPayload;
      if (response === postResponse) return { ok: true };
      if (response === roleToolsResponse) return emptyRoleToolsPayload;
      if (response === deleteResponse) return { ok: true };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<RolesAdminPage />);

    const table = screen.getByRole("table");
    expect(await within(table).findByText("admin")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add role" }));
    fireEvent.change(await screen.findByLabelText("Role name"), {
      target: { value: "support" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        "/admin/roles",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "content-type": "application/json",
          }),
          body: JSON.stringify({ name: "support" }),
        })
      );
    });

    expect(await within(table).findByText("support")).toBeInTheDocument();

    const supportRow = within(table).getByRole("row", { name: /manage support/i }).closest("tr");
    if (!supportRow) throw new Error("Missing support row");
    fireEvent.click(supportRow);

    fireEvent.click(await screen.findByRole("button", { name: "Delete role" }));
    const confirmDialog = await screen.findByRole("dialog", { name: "Delete role?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete role" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith("/admin/roles/support", {
        method: "DELETE",
      });
    });

    await waitFor(() => {
      expect(within(table).queryByText("support")).not.toBeInTheDocument();
    });
  });
});
