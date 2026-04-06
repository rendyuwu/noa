import type { AuthUser } from "@/components/lib/auth-store";

export function formatClaudeGreetingName(user: AuthUser | null): string {
  const display = user?.display_name?.trim();
  if (display) return display;

  const email = user?.email?.trim();
  if (email) return email.split("@")[0] || email;

  return "there";
}

export function getClaudeTimeGreeting(now = new Date()): "Morning" | "Afternoon" | "Evening" {
  const hour = now.getHours();
  if (hour < 12) return "Morning";
  if (hour < 18) return "Afternoon";
  return "Evening";
}
