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

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  adminOnly?: boolean;
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
