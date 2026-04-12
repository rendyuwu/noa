import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
  jsonOrThrow: vi.fn(),
}));

vi.mock("@/components/lib/fetch-helper", () => ({
  fetchWithAuth: (...args: any[]) => mocks.fetchWithAuth(...args),
  jsonOrThrow: (...args: any[]) => mocks.jsonOrThrow(...args),
}));

import { AuditReceiptPage } from "./audit-receipt-page";

describe("AuditReceiptPage", () => {
  beforeEach(() => {
    mocks.fetchWithAuth.mockReset();
    mocks.jsonOrThrow.mockReset();
  });

  it("renders the standalone receipt shell as an editorial surface", async () => {
    const payload = { actionRequestId: "action-1234" };
    const response = new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

    mocks.fetchWithAuth.mockResolvedValue(response);
    mocks.jsonOrThrow.mockImplementation(async (input: Response) => {
      if (input === response) return payload;
      throw new Error("Unexpected jsonOrThrow response");
    });

    const { container } = render(<AuditReceiptPage actionRequestId="action-1234" />);

    await screen.findByRole("button", { name: "Copy image" });

    expect(container.firstElementChild?.firstElementChild).toHaveClass("max-w-7xl");
    expect(screen.getByRole("heading", { name: "Receipt" })).toHaveClass("editorial-title");
    expect(screen.getByRole("button", { name: "Copy image" }).closest(".editorial-subpanel")).not.toBeNull();
  });
});
