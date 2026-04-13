import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AdminDetailModal } from "./admin-detail-modal";

describe("AdminDetailModal", () => {
  it("renders a shared scrollable body shell", () => {
    const onOpenChange = vi.fn();

    render(
      <AdminDetailModal
        open
        onOpenChange={onOpenChange}
        title="Server details"
        subtitle="Shared detail shell"
        footer={<button type="button">Footer action</button>}
      >
        <div>Body content</div>
      </AdminDetailModal>,
    );

    expect(screen.getByRole("dialog", { name: "Server details" })).toBeInTheDocument();

    const body = screen.getByTestId("admin-detail-modal-body");

    expect(body).toHaveClass("min-h-0");
    expect(body).toHaveClass("flex-1");
    expect(body).toHaveClass("overflow-y-auto");
    expect(body).toHaveClass("overscroll-contain");
  });
});
