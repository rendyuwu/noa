import { describe, expect, it } from "vitest";

import { isPlaceholderAdminRouteEnabled } from "./placeholder-route-access";

describe("isPlaceholderAdminRouteEnabled", () => {
  it("disables placeholder admin routes by default in production", () => {
    expect(isPlaceholderAdminRouteEnabled({ NODE_ENV: "production" })).toBe(false);
  });

  it("enables placeholder admin routes in non-production environments", () => {
    expect(isPlaceholderAdminRouteEnabled({ NODE_ENV: "development" })).toBe(true);
  });

  it("honors the production override flag", () => {
    expect(
      isPlaceholderAdminRouteEnabled({
        NODE_ENV: "production",
        NOA_ENABLE_PLACEHOLDER_ADMIN_SURFACES: "true",
      }),
    ).toBe(true);
  });
});
