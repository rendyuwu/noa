import type { AuthUser } from "@/components/lib/auth-store";

function titleCaseWord(value: string) {
  return value.slice(0, 1).toUpperCase() + value.slice(1).toLowerCase();
}

export function formatClaudeGreetingName(user: AuthUser | null): string {
  const display = user?.display_name?.trim();
  if (display) return display;

  const email = user?.email?.trim();
  if (email) {
    const localPart = email.split("@")[0] || email;
    const words = localPart
      .split(/[._-]+/)
      .map((part) => part.trim())
      .filter(Boolean)
      .map(titleCaseWord);

    if (words.length > 0) {
      return words.join(" ");
    }
  }

  return "there";
}

export function getClaudeTimeGreeting(now = new Date()): "Morning" | "Afternoon" | "Evening" {
  const hour = now.getHours();
  if (hour < 12) return "Morning";
  if (hour < 18) return "Afternoon";
  return "Evening";
}
