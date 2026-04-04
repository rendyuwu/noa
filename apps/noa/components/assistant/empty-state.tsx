"use client";

import { useMemo } from "react";
import { Globe, HardDrive, Server, Shield } from "lucide-react";

export function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 17) return "Good afternoon";
  if (hour >= 17 && hour < 21) return "Good evening";
  return "Hello, night owl";
}

type SuggestionPill = {
  label: string;
  prompt: string;
  icon: React.ComponentType<{ className?: string }>;
};

const suggestions: SuggestionPill[] = [
  {
    label: "Manage servers",
    prompt: "Show me the status of all WHM servers",
    icon: Server,
  },
  {
    label: "Check accounts",
    prompt: "Search for a cPanel account by domain",
    icon: HardDrive,
  },
  {
    label: "Firewall rules",
    prompt: "Help me manage firewall allowlist entries",
    icon: Shield,
  },
  {
    label: "DNS & domains",
    prompt: "Help me change the primary domain for an account",
    icon: Globe,
  },
];

export function EmptyState() {
  const greeting = useMemo(() => getGreeting(), []);

  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center px-4 text-center">
      {/* Brand mark */}
      <div className="mb-6 flex size-12 items-center justify-center rounded-2xl bg-accent text-lg font-bold text-accent-foreground shadow-sm">
        N
      </div>

      <h2 className="font-ui text-2xl font-semibold tracking-tight text-text">
        {greeting}
      </h2>
      <p className="mt-2 max-w-md font-ui text-sm text-muted">
        Start a conversation with NOA. Your threads are saved automatically.
      </p>

      {/* Suggestion pills */}
      <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
        {suggestions.map((pill) => (
          <button
            key={pill.label}
            type="button"
            className="flex items-center gap-2 rounded-full border border-border bg-surface px-4 py-2 font-ui text-sm text-muted shadow-sm transition hover:border-accent/40 hover:bg-surface-2 hover:text-text"
          >
            <pill.icon className="size-4" />
            {pill.label}
          </button>
        ))}
      </div>
    </div>
  );
}
