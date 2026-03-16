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

  it("DELETEs /admin/whm/servers/:id from the actions column", async () => {
    const serverId = "server-1";
    const listPayload = {
      servers: [
        {
          id: serverId,
          name: "web1",
          base_url: "https://whm.example.com:2087",
          api_username: "root",
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
    const row = (await within(table).findByText("web1")).closest("tr");
    if (!row) throw new Error("Missing server row");

    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(`/admin/whm/servers/${serverId}`, {
        method: "DELETE",
      });
    });

    await waitFor(() => {
      expect(within(table).queryByText("web1")).not.toBeInTheDocument();
    });
  });
});
