import type { LucideIcon } from "lucide-react";
import { Settings } from "lucide-react";

export type ChatNavAction = {
  label: string;
  icon: LucideIcon;
  href: string;
};

/**
 * Admin link shown in user-menu for admin users only.
 */
export const adminNavAction: ChatNavAction = {
  label: "Admin panel",
  icon: Settings,
  href: "/admin",
};
