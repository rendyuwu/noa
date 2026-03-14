import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  push: vi.fn(),
  setAuthToken: vi.fn(),
  setAuthUser: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mocks.push,
  }),
}));

vi.mock("@/components/lib/auth-store", () => ({
  setAuthToken: (...args: unknown[]) => mocks.setAuthToken(...args),
  setAuthUser: (...args: unknown[]) => mocks.setAuthUser(...args),
}));

import LoginPage from "./page";

describe("LoginPage", () => {
  beforeEach(() => {
    mocks.fetch.mockReset();
    mocks.push.mockReset();
    mocks.setAuthToken.mockReset();
    mocks.setAuthUser.mockReset();
    vi.stubGlobal("fetch", mocks.fetch);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows pending approval copy from error_code instead of backend detail prose", async () => {
    mocks.fetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "Approval request is still awaiting administrator review.",
          error_code: "user_pending_approval",
        }),
        {
          status: 403,
          headers: {
            "content-type": "application/json",
          },
        },
      ),
    );

    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "pending@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent(
      "Your account is pending approval. Ask an admin to enable it.",
    );
    expect(alert).not.toHaveTextContent(
      "Approval request is still awaiting administrator review.",
    );
  });
});
