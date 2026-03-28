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

import { WhmServersAdminPage } from "./whm-servers-admin-page";

describe("WhmServersAdminPage", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
  });

  it("creates a server with SSH key fields", async () => {
    const listPayload = { servers: [] };
    const createdServer = {
      id: "server-1",
      name: "web1",
      base_url: "https://whm.example.com:2087",
      api_username: "root",
      ssh_username: "ubuntu",
      ssh_port: 2222,
      ssh_host_key_fingerprint: null,
      has_ssh_password: false,
      has_ssh_private_key: true,
      verify_ssl: true,
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
      if (path === "/admin/whm/servers" && !init) return listResponse;
      if (path === "/admin/whm/servers" && init?.method === "POST") return createResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) return listPayload;
      if (response === createResponse) return { server: createdServer };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<WhmServersAdminPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Add server" }));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "web1" } });
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://whm.example.com:2087" },
    });
    fireEvent.change(screen.getByLabelText("API username"), { target: { value: "root" } });
    fireEvent.change(screen.getByLabelText("API token"), { target: { value: "WHM_TOKEN" } });
    fireEvent.click(screen.getByLabelText("Enable SSH"));
    fireEvent.change(screen.getByLabelText("SSH username"), { target: { value: "ubuntu" } });
    fireEvent.change(screen.getByLabelText("SSH port"), { target: { value: "2222" } });
    fireEvent.change(screen.getByLabelText("SSH private key"), {
      target: { value: "-----BEGIN OPENSSH PRIVATE KEY-----\nKEY\n-----END OPENSSH PRIVATE KEY-----" },
    });
    fireEvent.change(screen.getByLabelText("Key passphrase"), { target: { value: "passphrase" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        "/admin/whm/servers",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const createCall = mocks.fetchWithAuth.mock.calls.find(
      ([path, init]) => path === "/admin/whm/servers" && init?.method === "POST",
    );
    expect(createCall).toBeDefined();
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      name: "web1",
      base_url: "https://whm.example.com:2087",
      api_username: "root",
      api_token: "WHM_TOKEN",
      verify_ssl: true,
      ssh_username: "ubuntu",
      ssh_port: 2222,
      ssh_private_key: "-----BEGIN OPENSSH PRIVATE KEY-----\nKEY\n-----END OPENSSH PRIVATE KEY-----",
      ssh_private_key_passphrase: "passphrase",
    });
  });

  it("opens the drawer and DELETEs /admin/whm/servers/:id from the danger zone", async () => {
    const serverId = "server-1";
    const listPayload = {
      servers: [
        {
          id: serverId,
          name: "web1",
          base_url: "https://whm.example.com:2087",
          api_username: "root",
          ssh_username: null,
          ssh_port: null,
          ssh_host_key_fingerprint: null,
          has_ssh_password: false,
          has_ssh_private_key: false,
          verify_ssl: true,
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
      if (path === "/admin/whm/servers") return listResponse;
      if (path === `/admin/whm/servers/${serverId}` && init?.method === "DELETE") {
        return deleteResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) return listPayload;
      if (response === deleteResponse) return { ok: true };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<WhmServersAdminPage />);

    const table = screen.getByRole("table");
    const row = (await within(table).findByRole("row", { name: /manage web1/i })).closest("tr");
    if (!row) throw new Error("Missing server row");

    fireEvent.click(row);

    expect(await screen.findByText("Server details")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete server" }));

    const confirmDialog = await screen.findByRole("dialog", { name: "Delete server?" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete server" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(`/admin/whm/servers/${serverId}`, {
        method: "DELETE",
      });
    });

    await waitFor(() => {
      expect(within(table).queryByText("web1")).not.toBeInTheDocument();
    });
  });

  it("updates an existing server and can clear SSH configuration", async () => {
    const serverId = "server-1";
    const existingServer = {
      id: serverId,
      name: "web1",
      base_url: "https://whm.example.com:2087",
      api_username: "root",
      ssh_username: "ubuntu",
      ssh_port: 2222,
      ssh_host_key_fingerprint: "SHA256:abc",
      has_ssh_password: true,
      has_ssh_private_key: false,
      verify_ssl: true,
      updated_at: "2026-01-02T03:04:05.000Z",
    };
    const updatedServer = {
      ...existingServer,
      ssh_username: null,
      ssh_port: null,
      ssh_host_key_fingerprint: null,
      has_ssh_password: false,
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
      if (path === "/admin/whm/servers" && !init) return listResponse;
      if (path === `/admin/whm/servers/${serverId}` && init?.method === "PATCH") return patchResponse;
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) return { servers: [existingServer] };
      if (response === patchResponse) return { server: updatedServer };
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<WhmServersAdminPage />);

    const table = screen.getByRole("table");
    const row = (await within(table).findByRole("row", { name: /manage web1/i })).closest("tr");
    if (!row) throw new Error("Missing server row");

    fireEvent.click(row);
    fireEvent.click(await screen.findByRole("button", { name: "Edit server" }));

    fireEvent.click(screen.getByLabelText("Enable SSH"));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        `/admin/whm/servers/${serverId}`,
        expect.objectContaining({ method: "PATCH" }),
      );
    });

    const patchCall = mocks.fetchWithAuth.mock.calls.find(
      ([path, init]) => path === `/admin/whm/servers/${serverId}` && init?.method === "PATCH",
    );
    expect(patchCall).toBeDefined();
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({
      name: "web1",
      base_url: "https://whm.example.com:2087",
      api_username: "root",
      verify_ssl: true,
      clear_ssh_configuration: true,
    });

    expect(await screen.findByRole("status", { hidden: true })).toHaveTextContent(
      "Saved changes for web1.",
    );
  });

  it("shows validation failure messages to the user", async () => {
    const serverId = "server-1";
    const listPayload = {
      servers: [
        {
          id: serverId,
          name: "web1",
          base_url: "https://whm.example.com:2087",
          api_username: "root",
          ssh_username: null,
          ssh_port: null,
          ssh_host_key_fingerprint: null,
          has_ssh_password: false,
          has_ssh_private_key: false,
          verify_ssl: true,
          updated_at: "2026-01-02T03:04:05.000Z",
        },
      ],
    };

    const listResponse = new Response(JSON.stringify(listPayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const validateResponse = new Response(
      JSON.stringify({ ok: false, message: "SSH authentication failed" }),
      {
        status: 200,
        headers: { "content-type": "application/json" },
      },
    );

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/whm/servers" && !init) return listResponse;
      if (path === `/admin/whm/servers/${serverId}/validate` && init?.method === "POST") {
        return validateResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) return listPayload;
      if (response === validateResponse) {
        return { ok: false, message: "SSH authentication failed" };
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<WhmServersAdminPage />);

    const table = screen.getByRole("table");
    const row = (await within(table).findByRole("row", { name: /manage web1/i })).closest("tr");
    if (!row) throw new Error("Missing server row");

    fireEvent.click(row);
    fireEvent.click(await screen.findByRole("button", { name: "Validate" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("SSH authentication failed");
  });
});
