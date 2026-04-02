import { describe, expect, it } from "vitest";

import { getNavItems } from "./nav-items";

describe("navItems", () => {
  it("keeps unfinished admin placeholder routes out of the default navigation", () => {
    expect(getNavItems({ isAdmin: true, previewAdminRoutesEnabled: false }).map((item) => item.href)).toEqual([
      "/assistant",
      "/admin/users",
      "/admin/roles",
      "/login",
    ]);
  });
});
