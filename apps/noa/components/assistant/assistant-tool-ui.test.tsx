import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ToolFallback } from "./assistant-tool-ui";

describe("assistant tool UI", () => {
  it("hides raw payload details until requested", async () => {
    const user = userEvent.setup();

    render(
      <ToolFallback
        toolName="whm_preflight_account"
        status={{ type: "complete" }}
        argsText='{"server_ref":"srv-123"}'
        result={{ ok: true, user: "rendy" }}
      />,
    );

    expect(screen.getByText("whm_preflight_account")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show details" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByText(/server_ref/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"ok": true/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show details" }));

    expect(screen.getByRole("button", { name: "Hide details" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByText(/server_ref/)).toBeInTheDocument();
    expect(screen.getByText(/"ok": true/)).toBeInTheDocument();
  });
});
