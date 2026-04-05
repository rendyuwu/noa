import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/components/lib/http/fetch-client", () => ({
  ApiError: class ApiError extends Error {},
  fetchWithAuth: (...args: unknown[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: unknown[]) => mocks.jsonOrThrow(...args),
}));

vi.mock("sonner", () => ({
  toast: mocks.toast,
}));

import { WhmServersAdminPage } from "./whm-servers-admin-page";

describe("WhmServersAdminPage", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
    vi.spyOn(window, "confirm").mockReturnValue(true);
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

    const listResponse = new Response(null, { status: 200 });
    const createResponse = new Response(null, { status: 201 });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/whm/servers" && !init) {
        return listResponse;
      }
      if (path === "/admin/whm/servers" && init?.method === "POST") {
        return createResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) {
        return listPayload;
      }
      if (response === createResponse) {
        return { server: createdServer };
      }
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

    const listResponse = new Response(null, { status: 200 });
    const patchResponse = new Response(null, { status: 200 });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/whm/servers" && !init) {
        return listResponse;
      }
      if (path === `/admin/whm/servers/${serverId}` && init?.method === "PATCH") {
        return patchResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) {
        return { servers: [existingServer] };
      }
      if (response === patchResponse) {
        return { server: updatedServer };
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<WhmServersAdminPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Manage web1" }));
    expect(await screen.findByRole("button", { name: "Manage web1" })).toBeInTheDocument();
    expect(screen.getAllByText("web1")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Edit server" }));

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
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({
      name: "web1",
      base_url: "https://whm.example.com:2087",
      api_username: "root",
      verify_ssl: true,
      clear_ssh_configuration: true,
    });

    await waitFor(() => {
      expect(mocks.toast.success).toHaveBeenCalledWith("Saved changes for web1.");
    });
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
    const validatePayload = { ok: false, message: "SSH authentication failed" };

    const listResponse = new Response(null, { status: 200 });
    const validateResponse = new Response(null, { status: 200 });

    let listCalls = 0;

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/whm/servers" && !init) {
        listCalls += 1;
        return listResponse;
      }
      if (path === `/admin/whm/servers/${serverId}/validate` && init?.method === "POST") {
        return validateResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) {
        return listPayload;
      }
      if (response === validateResponse) {
        return validatePayload;
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<WhmServersAdminPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Manage web1" }));
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(`/admin/whm/servers/${serverId}/validate`, {
        method: "POST",
      });
    });

    expect(await screen.findByText("SSH authentication failed")).toBeInTheDocument();
    expect(listCalls).toBeGreaterThanOrEqual(2);
  });

  it("renders validation failures in an alert", async () => {
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
    const validatePayload = { ok: false, message: "SSH key fingerprint mismatch" };

    const listResponse = new Response(null, { status: 200 });
    const validateResponse = new Response(null, { status: 200 });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/whm/servers" && !init) {
        return listResponse;
      }
      if (path === `/admin/whm/servers/${serverId}/validate` && init?.method === "POST") {
        return validateResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) {
        return listPayload;
      }
      if (response === validateResponse) {
        return validatePayload;
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<WhmServersAdminPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Manage web1" }));
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("SSH key fingerprint mismatch");
  });

  it("keeps a remaining server selected after deleting the current selection", async () => {
    const firstServer = {
      id: "server-1",
      name: "web1",
      base_url: "https://whm-1.example.com:2087",
      api_username: "root",
      ssh_username: null,
      ssh_port: null,
      ssh_host_key_fingerprint: null,
      has_ssh_password: false,
      has_ssh_private_key: false,
      verify_ssl: true,
      updated_at: "2026-01-02T03:04:05.000Z",
    };
    const secondServer = {
      ...firstServer,
      id: "server-2",
      name: "web2",
      base_url: "https://whm-2.example.com:2087",
    };

    const listResponse = new Response(null, { status: 200 });
    const deleteResponse = new Response(null, { status: 200 });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/whm/servers" && !init) {
        return listResponse;
      }
      if (path === "/admin/whm/servers/server-1" && init?.method === "DELETE") {
        return deleteResponse;
      }
      throw new Error(`Unexpected fetchWithAuth path: ${path}`);
    });

    mocks.jsonOrThrow.mockImplementation(async (response: Response) => {
      if (response === listResponse) {
        return { servers: [firstServer, secondServer] };
      }
      if (response === deleteResponse) {
        return { ok: true };
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<WhmServersAdminPage />);

    expect(await screen.findByRole("button", { name: "Manage web1" })).toBeInTheDocument();
    expect(screen.getAllByText("web1")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "Delete server" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith("/admin/whm/servers/server-1", { method: "DELETE" });
    });

    await waitFor(() => {
      expect(screen.getAllByText("web2")).toHaveLength(2);
    });
    expect(screen.queryByText("No server selected")).not.toBeInTheDocument();
  });
});
