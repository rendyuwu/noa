import { render, screen, waitFor } from "@testing-library/react";
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

import { AuditReceiptPage } from "./audit-receipt-page";

describe("AuditReceiptPage", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
  });

  it("fetches and renders receipt payload", async () => {
    const response = new Response(null, { status: 200 });
    const payload = {
      replyTemplate: {
        title: "Server updated",
        outcome: "changed",
        summary: "Applied configuration changes.",
      },
      evidenceSections: [
        { title: "Verification", items: [{ label: "HTTP", value: "200" }] },
      ],
    };

    mocks.fetchWithAuth.mockResolvedValue(response);
    mocks.jsonOrThrow.mockResolvedValue(payload);

    render(<AuditReceiptPage actionRequestId="ar-1" />);

    await waitFor(() => {
      expect(mocks.fetchWithAuth).toHaveBeenCalledWith("/admin/audit/action-requests/ar-1/receipt");
    });

    expect(await screen.findByText("Server updated")).toBeInTheDocument();
    expect(screen.getByText("SUCCESS")).toBeInTheDocument();
    expect(screen.getByText("Verification")).toBeInTheDocument();
  });

  it("surfaces loading errors", async () => {
    mocks.fetchWithAuth.mockRejectedValue(new Error("Network down"));

    render(<AuditReceiptPage actionRequestId="ar-2" />);

    const alerts = await screen.findAllByRole("alert");

    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent("Network down");
    expect(screen.queryByText("Receipt unavailable.")).not.toBeInTheDocument();
  });
});
