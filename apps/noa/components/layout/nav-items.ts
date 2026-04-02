import type { LucideIcon } from "lucide-react";
import {
  Bot,
  ClipboardList,
  Database,
  HardDrive,
  KeyRound,
  Shield,
  Users,
} from "lucide-react";

import { isPlaceholderAdminRouteEnabled } from "@/components/admin/lib/placeholder-route-access";

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  surface?: "stable" | "preview";
};

export const navItems: NavItem[] = [
  { href: "/assistant", label: "Assistant", icon: Bot },
  { href: "/admin/users", label: "Users", icon: Users, adminOnly: true },
  { href: "/admin/roles", label: "Roles", icon: Shield, adminOnly: true },
  { href: "/admin/audit", label: "Audit", icon: ClipboardList, adminOnly: true },
  { href: "/admin/whm/servers", label: "WHM", icon: HardDrive, adminOnly: true },
  { href: "/admin/proxmox/servers", label: "Proxmox", icon: Database, adminOnly: true },
  { href: "/login", label: "Sign out", icon: KeyRound },
];

export function isNavItemVisible(
  item: NavItem,
  options: {
    isAdmin: boolean;
    previewAdminRoutesEnabled?: boolean;
  },
) {
  if (item.adminOnly && !options.isAdmin) {
    return false;
  }

  if (
    item.surface === "preview" &&
    !(options.previewAdminRoutesEnabled ?? isPlaceholderAdminRouteEnabled())
  ) {
    return false;
  }

  return true;
}

export function getNavItems({
  isAdmin = false,
  previewAdminRoutesEnabled,
}: {
  isAdmin?: boolean;
  previewAdminRoutesEnabled?: boolean;
} = {}) {
  return navItems.filter((item) => isNavItemVisible(item, { isAdmin, previewAdminRoutesEnabled }));
}
