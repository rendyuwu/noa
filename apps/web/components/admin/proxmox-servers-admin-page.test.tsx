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

import { ProxmoxServersAdminPage } from "./proxmox-servers-admin-page";

describe("ProxmoxServersAdminPage", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
  });

  it("loads and renders the server list", async () => {
    const listPayload = {
      servers: [
        {
          id: "server-1",
          name: "pve1",
          base_url: "https://pve1.example.com:8006",
          api_token_id: "root@pam!noa",
          has_api_token_secret: true,
          verify_ssl: false,
          updated_at: "2026-01-02T03:04:05.000Z",
        },
      ],
    };

    const listResponse = new Response(JSON.stringify(listPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

    mocks.fetchWithAuth.mockResolvedValue(listResponse);
    mocks.jsonOrThrow.mockResolvedValue(listPayload);

    render(<ProxmoxServersAdminPage />);

    const table = screen.getByRole("table");
    expect(await within(table).findByText("pve1")).toBeInTheDocument();
    expect(within(table).getByText("root@pam!noa")).toBeInTheDocument();
    expect(within(table).getByText("off")).toBeInTheDocument();
  });

  it("creates a server with the expected POST body", async () => {
    const listPayload = { servers: [] };
    const createdServer = {
      id: "server-1",
      name: "pve1",
      base_url: "https://pve1.example.com:8006",
      api_token_id: "root@pam!noa",
      has_api_token_secret: true,
      verify_ssl: false,
      updated_at: "2026-01-02T03:04:05.000Z",
    };

    const listResponse = new Response(JSON.stringify(listPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const createResponse = new Response(JSON.stringify({ server: createdServer }), {
      status: 201,
      headers: { "content-type": "application/json" },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/proxmox/servers" && !init) return listResponse;
      if (path === "/admin/proxmox/servers" && init?.method === "POST") return createResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) return listPayload;
      if (response === createResponse) return { server: createdServer };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<ProxmoxServersAdminPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Add server" }));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "pve1" } });
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://pve1.example.com:8006" },
    });
    fireEvent.change(screen.getByLabelText("API token ID"), {
      target: { value: "root@pam!noa" },
    });
    fireEvent.change(screen.getByLabelText("API token secret"), {
      target: { value: "SECRET_TOKEN" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        "/admin/proxmox/servers",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const createCall = mocks.fetchWithAuth.mock.calls.find(
      ([path, init]) => path === "/admin/proxmox/servers" && init?.method === "POST",
    );
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      name: "pve1",
      base_url: "https://pve1.example.com:8006",
      api_token_id: "root@pam!noa",
      api_token_secret: "SECRET_TOKEN",
      verify_ssl: false,
    });
  });

  it("updates an existing server with the expected PATCH body", async () => {
    const serverId = "server-1";
    const existingServer = {
      id: serverId,
      name: "pve1",
      base_url: "https://pve1.example.com:8006",
      api_token_id: "root@pam!old",
      has_api_token_secret: true,
      verify_ssl: false,
      updated_at: "2026-01-02T03:04:05.000Z",
    };
    const updatedServer = {
      ...existingServer,
      api_token_id: "root@pam!new",
      verify_ssl: true,
      updated_at: "2026-01-03T03:04:05.000Z",
    };

    const listResponse = new Response(JSON.stringify({ servers: [existingServer] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const patchResponse = new Response(JSON.stringify({ server: updatedServer }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/proxmox/servers" && !init) return listResponse;
      if (path === `/admin/proxmox/servers/${serverId}` && init?.method === "PATCH") {
        return patchResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) return { servers: [existingServer] };
      if (response === patchResponse) return { server: updatedServer };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<ProxmoxServersAdminPage />);

    const table = screen.getByRole("table");
    const row = (await within(table).findByRole("row", { name: /manage pve1/i })).closest("tr");
    if (!row) throw new Error("Missing server row");

    fireEvent.click(row);
    fireEvent.click(await screen.findByRole("button", { name: "Edit server" }));

    fireEvent.change(screen.getByLabelText("API token ID"), {
      target: { value: "root@pam!new" },
    });
    fireEvent.click(screen.getByLabelText("Verify SSL"));
    fireEvent.change(screen.getByLabelText("API token secret"), {
      target: { value: "NEW_SECRET" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        `/admin/proxmox/servers/${serverId}`,
        expect.objectContaining({ method: "PATCH" }),
      );
    });

    const patchCall = mocks.fetchWithAuth.mock.calls.find(
      ([path, init]) => path === `/admin/proxmox/servers/${serverId}` && init?.method === "PATCH",
    );
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({
      name: "pve1",
      base_url: "https://pve1.example.com:8006",
      api_token_id: "root@pam!new",
      api_token_secret: "NEW_SECRET",
      verify_ssl: true,
    });
  });

  it("POSTs to validate the selected server", async () => {
    const serverId = "server-1";
    const listPayload = {
      servers: [
        {
          id: serverId,
          name: "pve1",
          base_url: "https://pve1.example.com:8006",
          api_token_id: "root@pam!noa",
          has_api_token_secret: true,
          verify_ssl: false,
          updated_at: "2026-01-02T03:04:05.000Z",
        },
      ],
    };
    const validatePayload = { ok: true, message: "Connection OK" };

    const listResponse = new Response(JSON.stringify(listPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const validateResponse = new Response(JSON.stringify(validatePayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

    let listCalls = 0;
    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/proxmox/servers" && !init) {
        listCalls += 1;
        return listResponse;
      }
      if (path === `/admin/proxmox/servers/${serverId}/validate` && init?.method === "POST") {
        return validateResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) return listPayload;
      if (response === validateResponse) return validatePayload;
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<ProxmoxServersAdminPage />);

    const table = screen.getByRole("table");
    const row = (await within(table).findByRole("row", { name: /manage pve1/i })).closest("tr");
    if (!row) throw new Error("Missing server row");

    fireEvent.click(row);
    fireEvent.click(await screen.findByRole("button", { name: "Validate" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        `/admin/proxmox/servers/${serverId}/validate`,
        { method: "POST" },
      );
    });
    expect(listCalls).toBeGreaterThanOrEqual(2);
  });

  it("DELETEs the selected server from the danger zone", async () => {
    const serverId = "server-1";
    const listPayload = {
      servers: [
        {
          id: serverId,
          name: "pve1",
          base_url: "https://pve1.example.com:8006",
          api_token_id: "root@pam!noa",
          has_api_token_secret: true,
          verify_ssl: false,
          updated_at: "2026-01-02T03:04:05.000Z",
        },
      ],
    };

    const listResponse = new Response(JSON.stringify(listPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const deleteResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/proxmox/servers" && !init) return listResponse;
      if (path === `/admin/proxmox/servers/${serverId}` && init?.method === "DELETE") {
        return deleteResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) return listPayload;
      if (response === deleteResponse) return { ok: true };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<ProxmoxServersAdminPage />);

    const table = screen.getByRole("table");
    const row = (await within(table).findByRole("row", { name: /manage pve1/i })).closest("tr");
    if (!row) throw new Error("Missing server row");

    fireEvent.click(row);
    fireEvent.click(await screen.findByRole("button", { name: "Delete server" }));

    const confirmDialog = await screen.findByRole("dialog", { name: "Delete server?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete server" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(`/admin/proxmox/servers/${serverId}`, {
        method: "DELETE",
      });
    });

    await waitFor(() => {
      expect(within(table).queryByText("pve1")).not.toBeInTheDocument();
    });
  });
});
