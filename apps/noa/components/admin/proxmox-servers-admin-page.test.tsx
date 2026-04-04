import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
}));

vi.mock("@/components/lib/http/fetch-client", () => ({
  ApiError: class ApiError extends Error {},
  fetchWithAuth: (...args: unknown[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: unknown[]) => mocks.jsonOrThrow(...args),
}));

import { ProxmoxServersAdminPage } from "./proxmox-servers-admin-page";

describe("ProxmoxServersAdminPage", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
    vi.spyOn(window, "confirm").mockReturnValue(true);
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

    const listResponse = new Response(null, { status: 200 });
    mocks.fetchWithAuth.mockResolvedValue(listResponse);
    mocks.jsonOrThrow.mockResolvedValue(listPayload);

    render(<ProxmoxServersAdminPage />);

    expect(await screen.findByText("pve1")).toBeInTheDocument();
    expect(screen.getByText("root@pam!noa")).toBeInTheDocument();
    expect(screen.getByText("off")).toBeInTheDocument();
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

    const listResponse = new Response(null, { status: 200 });
    const createResponse = new Response(null, { status: 201 });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/proxmox/servers" && !init) {
        return listResponse;
      }
      if (path === "/admin/proxmox/servers" && init?.method === "POST") {
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

    const listResponse = new Response(null, { status: 200 });
    const patchResponse = new Response(null, { status: 200 });

    mocks.fetchWithAuth.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/admin/proxmox/servers" && !init) {
        return listResponse;
      }
      if (path === `/admin/proxmox/servers/${serverId}` && init?.method === "PATCH") {
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

    render(<ProxmoxServersAdminPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Manage pve1" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit server" }));

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

    const listResponse = new Response(null, { status: 200 });
    const validateResponse = new Response(null, { status: 200 });

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
      if (response === listResponse) {
        return listPayload;
      }
      if (response === validateResponse) {
        return validatePayload;
      }
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<ProxmoxServersAdminPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Manage pve1" }));
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        `/admin/proxmox/servers/${serverId}/validate`,
        { method: "POST" },
      );
    });

    expect(listCalls).toBeGreaterThanOrEqual(2);
  });
});
