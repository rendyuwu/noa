import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdminListLayout } from "./admin-list-layout";

describe("AdminListLayout", () => {
  it("treats the header, filter band, and empty state as editorial surfaces", () => {
    const { container } = render(
      <AdminListLayout
        title="Editorial Users"
        description="Manage access with the editorial shell."
        filter={<div data-testid="filter-surface">Filter</div>}
        empty
        emptyTitle="No users yet"
        emptyDescription="Users will appear here after they sign in."
      >
        <div>Table body</div>
      </AdminListLayout>,
    );

    expect(container.firstElementChild).toHaveClass("max-w-7xl");
    expect(screen.getByRole("heading", { name: "Editorial Users" })).toHaveClass("font-serif");

    expect(screen.getByTestId("filter-surface").parentElement).toHaveClass(
      "rounded-2xl",
      "border",
      "border-border/80",
      "bg-card/80",
      "shadow-sm",
    );

    expect(screen.getByText("No users yet").parentElement?.parentElement).toHaveClass(
      "rounded-2xl",
      "border",
      "border-border/80",
      "bg-card/80",
      "shadow-sm",
    );
    expect(screen.queryByText("Table body")).not.toBeInTheDocument();
  });
});
