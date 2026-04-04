import type { LucideIcon } from "lucide-react";
import {
  Bot,
  ClipboardList,
  Database,
  HardDrive,
  Shield,
  Users,
} from "lucide-react";

export type AdminNavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
};

export const adminNavItems: AdminNavItem[] = [
  { href: "/admin/users", label: "Users", icon: Users },
  { href: "/admin/roles", label: "Roles", icon: Shield },
  { href: "/admin/audit", label: "Audit", icon: ClipboardList },
  { href: "/admin/whm/servers", label: "WHM", icon: HardDrive },
  { href: "/admin/proxmox/servers", label: "Proxmox", icon: Database },
];

export const backToChatAction = {
  label: "Back to chat",
  icon: Bot,
  href: "/assistant",
};
