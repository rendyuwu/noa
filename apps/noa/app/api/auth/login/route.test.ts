import { afterEach, describe, expect, it, vi } from "vitest";

import { POST as loginRoute } from "./route";
import { GET as meRoute } from "../me/route";
import { POST as logoutRoute } from "../logout/route";

describe("BFF auth routes", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("sets auth and csrf cookies on successful login", async () => {
    vi.stubEnv("NOA_API_URL", "http://backend:8000");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          access_token: "access-token-123",
          expires_in: 3600,
          user: { id: "user-1", email: "user@example.com" },
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );

    const response = await loginRoute(
      new Request("http://localhost:3000/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: "user@example.com", password: "secret" }),
      }),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain("noa_session=");
    expect(response.headers.get("set-cookie")).toContain("noa_csrf=");
  });

  it("returns 401 from /api/auth/me when the auth cookie is missing", async () => {
    const response = await meRoute(new Request("http://localhost:3000/api/auth/me"));
    expect(response.status).toBe(401);
  });

  it("clears auth cookies on logout", async () => {
    const response = await logoutRoute(
      new Request("http://localhost:3000/api/auth/logout", { method: "POST" }),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
  });
});
