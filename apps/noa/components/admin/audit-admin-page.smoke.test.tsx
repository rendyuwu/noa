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

import { AuditAdminPage } from "./audit-admin-page";

describe("AuditAdminPage smoke", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
  });

  it("loads audit items and renders receipt links", async () => {
    const listResponse = new Response(null, { status: 200 });
    const listPayload = {
      items: [
        {
          actionRequestId: "ar-1",
          threadId: "thread-1",
          toolName: "whm_suspend_account",
          risk: "CHANGE",
          status: "PENDING",
          createdAt: "2026-01-01T00:00:00.000Z",
          updatedAt: "2026-01-01T00:00:00.000Z",
          hasReceipt: true,
        },
      ],
      nextCursor: null,
    };

    mocks.fetchWithAuth.mockResolvedValue(listResponse);
    mocks.jsonOrThrow.mockResolvedValue(listPayload);

    render(<AuditAdminPage />);

    expect(screen.getByRole("button", { name: "Show advanced filters" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply filters" })).toBeInTheDocument();
    expect(screen.getByLabelText("Tool")).toBeInTheDocument();
    expect(screen.getByLabelText("Status")).toBeInTheDocument();
    expect(screen.getByLabelText("From")).toBeInTheDocument();
    expect(screen.getByLabelText("To")).toBeInTheDocument();
    expect(screen.queryByLabelText("Limit")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show advanced filters" }));
    expect(screen.getByRole("button", { name: "Hide advanced filters" })).toBeInTheDocument();
    expect(screen.getByLabelText("Limit")).toBeInTheDocument();

    expect(await screen.findByText("whm_suspend_account")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View receipt" })).toHaveAttribute(
      "href",
      "/admin/audit/receipts/ar-1",
    );
  });

  it("applies filters and sends them to the audit list endpoint", async () => {
    const listResponse = new Response(null, { status: 200 });
    mocks.fetchWithAuth.mockResolvedValue(listResponse);
    mocks.jsonOrThrow.mockResolvedValue({ items: [], nextCursor: null });

    render(<AuditAdminPage />);

    await screen.findByText("No matching action requests.");

    fireEvent.change(screen.getByLabelText("Tool"), { target: { value: "whm_suspend_account" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith(
        expect.stringContaining("/admin/audit/action-requests?"),
      );
    });

    const matchingPath = mocks.fetchWithAuth.mock.calls
      .map(([path]) => String(path))
      .find((path) => path.includes("toolName=whm_suspend_account"));

    expect(matchingPath).toBeDefined();
  });
});
