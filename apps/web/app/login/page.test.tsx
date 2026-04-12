import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    window.history.replaceState({}, "", "/login");
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

  it("shows contextual fallback copy for unexpected login errors", async () => {
    mocks.fetch.mockRejectedValue(new Error("socket hang up"));

    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "pending@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent("Login failed");
    expect(alert).not.toHaveTextContent("socket hang up");
  });

  it("shows inline field validation errors and does not submit when fields are empty", async () => {
    render(<LoginPage />);

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Email is required.")).toBeInTheDocument();
    expect(await screen.findByText("Password is required.")).toBeInTheDocument();
    expect(mocks.fetch).not.toHaveBeenCalled();
  });

  it("renders the editorial shell with a serif heading", () => {
    const { container } = render(<LoginPage />);

    expect(container.querySelector("form")).toHaveClass("rounded-[32px]");
    expect(container.querySelector("form")).toHaveClass("bg-card/80");
    expect(screen.getByRole("heading", { name: "Login" })).toHaveClass("font-serif");
  });

  it("shows inline email format validation error and does not submit", async () => {
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "not-an-email" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.queryByText("Password is required.")).not.toBeInTheDocument();
    expect(mocks.fetch).not.toHaveBeenCalled();
  });

  it("redirects to returnTo after successful login", async () => {
    window.history.replaceState(
      {},
      "",
      "/login?returnTo=/assistant/49870dcd-933a-4a6e-a605-7f302b82d9a2",
    );

    mocks.fetch.mockResolvedValue(
      new Response(JSON.stringify({ access_token: "token", user: null }), {
        status: 200,
        headers: {
          "content-type": "application/json",
        },
      }),
    );

    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(mocks.push).toHaveBeenCalledWith(
        "/assistant/49870dcd-933a-4a6e-a605-7f302b82d9a2",
      );
    });
  });
});
