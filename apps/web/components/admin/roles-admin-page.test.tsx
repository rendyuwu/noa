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

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

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
    const memberRoleToolsPayload = {
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
    const roleToolsResponse = new Response(JSON.stringify(roleToolsPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const memberRoleToolsResponse = new Response(JSON.stringify(memberRoleToolsPayload), {
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
      if (path === "/admin/roles/member/tools" && !init) return memberRoleToolsResponse;
      if (path === "/admin/roles/admin/tools" && !init) return roleToolsResponse;
      if (path === "/admin/roles/admin/tools" && init?.method === "PUT") return putResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === rolesResponse) return rolesPayload;
      if (response === toolsResponse) return toolsPayload;
      if (response === memberRoleToolsResponse) return memberRoleToolsPayload;
      if (response === roleToolsResponse) return roleToolsPayload;
      if (response === putResponse) return { ok: true };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<RolesAdminPage />);

    const table = await screen.findByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Role" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Tools" })).toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: "Summary" })).not.toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: "State" })).not.toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: "Access" })).not.toBeInTheDocument();
    expect(within(table).getByText("1 tool assigned")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Manage admin" }));

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
    const adminRoleToolsPayload = {
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
    const postResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const adminRoleToolsResponse = new Response(JSON.stringify(adminRoleToolsPayload), {
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
      if (path === "/admin/roles/admin/tools" && !init) return adminRoleToolsResponse;
      if (path === "/admin/roles" && init?.method === "POST") return postResponse;
      if (path === "/admin/roles/support/tools" && !init) return roleToolsResponse;
      if (path === "/admin/roles/support" && init?.method === "DELETE") return deleteResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === rolesResponse) return rolesPayload;
      if (response === toolsResponse) return toolsPayload;
      if (response === adminRoleToolsResponse) return adminRoleToolsPayload;
      if (response === postResponse) return { ok: true };
      if (response === roleToolsResponse) return emptyRoleToolsPayload;
      if (response === deleteResponse) return { ok: true };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<RolesAdminPage />);

    expect(await screen.findByText("admin")).toBeInTheDocument();

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

    expect(await screen.findByText("support")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Manage support" }));

    fireEvent.click(await screen.findByRole("button", { name: "Delete role" }));
    const confirmDialog = await screen.findByRole("dialog", { name: "Delete role?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete role" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith("/admin/roles/support", {
        method: "DELETE",
      });
    });

    await waitFor(() => {
      expect(screen.queryByText("support")).not.toBeInTheDocument();
    });
  });

  it("migrates legacy direct grants via POST /admin/migrations/direct-grants", async () => {
    const rolesPayload = {
      roles: [{ name: "admin" }],
    };
    const toolsPayload = {
      tools: ["get_current_time"],
    };
    const roleToolsPayload = {
      tools: ["get_current_time"],
    };
    const migratePayload = {
      users_migrated: 2,
      roles_created: 1,
      roles_reused: 3,
    };

    const rolesResponse = new Response(JSON.stringify(rolesPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const toolsResponse = new Response(JSON.stringify(toolsPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const migrateResponse = new Response(JSON.stringify(migratePayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const roleToolsResponse = new Response(JSON.stringify(roleToolsPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/roles") return rolesResponse;
      if (path === "/admin/tools") return toolsResponse;
      if (path === "/admin/roles/admin/tools" && !init) return roleToolsResponse;
      if (path === "/admin/migrations/direct-grants" && init?.method === "POST") return migrateResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === rolesResponse) return rolesPayload;
      if (response === toolsResponse) return toolsPayload;
      if (response === roleToolsResponse) return roleToolsPayload;
      if (response === migrateResponse) return migratePayload;
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<RolesAdminPage />);

    const migrateButton = await screen.findByRole("button", { name: "Migrate legacy direct grants" });
    await waitFor(() => {
      expect(migrateButton).not.toBeDisabled();
    });
    fireEvent.click(migrateButton);
    const confirmDialog = await screen.findByRole("dialog", { name: "Migrate legacy direct grants?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Migrate" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith("/admin/migrations/direct-grants", {
        method: "POST",
      });
    });

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("2 users migrated");
    expect(status).toHaveTextContent("1 role created");
    expect(status).toHaveTextContent("3 roles reused");
  });

  it("does not let a stale background count overwrite a saved count", async () => {
    const rolesPayload = {
      roles: [{ name: "admin" }],
    };
    const toolsPayload = {
      tools: ["get_current_time", "set_demo_flag"],
    };
    const staleRoleToolsPayload = {
      tools: ["get_current_time"],
    };
    const currentRoleToolsPayload = {
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
    const staleRoleToolsResponse = new Response(JSON.stringify(staleRoleToolsPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const currentRoleToolsResponse = new Response(JSON.stringify(currentRoleToolsPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const putResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const staleBackgroundRequest = deferredResponse();
    let adminToolsGetCount = 0;

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/roles") return rolesResponse;
      if (path === "/admin/tools") return toolsResponse;
      if (path === "/admin/roles/admin/tools" && !init) {
        adminToolsGetCount += 1;
        return adminToolsGetCount === 1 ? staleBackgroundRequest.promise : currentRoleToolsResponse;
      }
      if (path === "/admin/roles/admin/tools" && init?.method === "PUT") return putResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === rolesResponse) return rolesPayload;
      if (response === toolsResponse) return toolsPayload;
      if (response === staleRoleToolsResponse) return staleRoleToolsPayload;
      if (response === currentRoleToolsResponse) return currentRoleToolsPayload;
      if (response === putResponse) return { ok: true };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<RolesAdminPage />);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("—")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Manage admin" }));
    fireEvent.click(await screen.findByLabelText("set_demo_flag"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(within(table).getByText("2 tools assigned")).toBeInTheDocument();
    });

    staleBackgroundRequest.resolve(staleRoleToolsResponse);

    await waitFor(() => {
      expect(mocks.jsonOrThrow).toHaveBeenCalledWith(staleRoleToolsResponse);
      expect(within(table).getByText("2 tools assigned")).toBeInTheDocument();
    });
    expect(within(table).queryByText("1 tool assigned")).not.toBeInTheDocument();
  });
});
