import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
}));

vi.mock("@/components/lib/fetch-helper", () => ({
  fetchWithAuth: (...args: any[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: any[]) => mocks.jsonOrThrow(...args),
}));

import { AuditAdminPage } from "./audit-admin-page";

describe("AuditAdminPage", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
  });

  it("renders the shared header shell, filters, and audit table", async () => {
    const payload = {
      items: [
        {
          actionRequestId: "action-1234",
          threadId: "thread-5678",
          toolRunId: "toolrun-9999",
          receiptId: "receipt-1111",
          toolName: "whm_create_account",
          risk: "CHANGE",
          status: "APPROVED",
          requestedByEmail: "admin@example.com",
          decidedAt: "2026-04-10T11:12:13.000Z",
          createdAt: "2026-04-10T10:11:12.000Z",
          updatedAt: "2026-04-10T11:12:13.000Z",
          terminalPhase: "completed",
          hasReceipt: true,
        },
      ],
      nextCursor: null,
    };

    const response = new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

    mocks.fetchWithAuth.mockResolvedValue(response);
    mocks.jsonOrThrow.mockImplementation(async (input: Response) => {
      if (input === response) return payload;
      throw new Error("Unexpected jsonOrThrow response");
    });

    render(<AuditAdminPage />);

    expect(screen.getByRole("heading", { name: "Audit" })).toBeInTheDocument();
    expect(screen.getByText("Review approvals, executions, and receipts across all threads.")).toBeInTheDocument();

    const filtersButton = await screen.findByRole("button", { name: "Filters" });
    fireEvent.click(filtersButton);
    expect(await screen.findByRole("combobox", { name: "Status" })).toBeInTheDocument();

    const table = await screen.findByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Created" })).toBeInTheDocument();
    expect(within(table).getByText("Create Account")).toBeInTheDocument();
    expect(within(table).getByText("Finished")).toBeInTheDocument();
  });
});
