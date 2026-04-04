import { describe, expect, it } from "vitest";

import { getNavItems } from "./nav-items";

describe("navItems", () => {
  it("shows only public routes for non-admins", () => {
    expect(getNavItems().map((item) => item.href)).toEqual(["/assistant", "/login"]);
  });

  it("shows admin routes for admins", () => {
    expect(getNavItems({ isAdmin: true }).map((item) => item.href)).toEqual([
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
