import { describe, expect, it } from "vitest";

import { getNavItems } from "./nav-items";

describe("navItems", () => {
  it("shows parity-complete admin routes in the default navigation for admins", () => {
    expect(getNavItems({ isAdmin: true, previewAdminRoutesEnabled: false }).map((item) => item.href)).toEqual([
      "/assistant",
      "/admin/users",
      "/admin/roles",
      "/admin/audit",
      "/admin/whm/servers",
      "/admin/proxmox/servers",
      "/login",
    ]);
  });
});
