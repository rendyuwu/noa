import { describe, expect, it } from "vitest";

import { buildSecurityHeaders } from "./http-headers";

describe("http-headers", () => {
  it("includes baseline production security headers", () => {
    const headers = buildSecurityHeaders({ isProduction: true });
    expect(headers).toContainEqual({ key: "X-Frame-Options", value: "DENY" });
    expect(headers).toContainEqual({ key: "X-Content-Type-Options", value: "nosniff" });
    expect(headers).toContainEqual({
      key: "Referrer-Policy",
      value: "strict-origin-when-cross-origin",
    });
  });

  it("includes a CSP header", () => {
    const headers = buildSecurityHeaders({ isProduction: true });
    expect(headers.find((header) => header.key === "Content-Security-Policy")?.value).toContain(
      "default-src 'self'",
    );
  });
});
