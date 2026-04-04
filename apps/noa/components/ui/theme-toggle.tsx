"use client";

import { useEffect, useState } from "react";
import { MoonStar, SunMedium, SunMoon } from "lucide-react";
import { useTheme } from "next-themes";

import { cn } from "@/components/lib";
import { Button } from "@/components/ui/button";

type ThemeToggleProps = {
  className?: string;
};

export function ThemeToggle({ className }: ThemeToggleProps) {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const activeTheme = mounted ? resolvedTheme ?? theme : undefined;
  const isDark = activeTheme === "dark";
  const label = mounted ? (isDark ? "Switch to light mode" : "Switch to dark mode") : "Toggle theme";
  const Icon = mounted ? (isDark ? SunMedium : MoonStar) : SunMoon;

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className={cn("rounded-lg border border-border bg-surface text-muted hover:bg-surface-2", className)}
      aria-label={label}
      disabled={!mounted}
      onClick={() => setTheme(isDark ? "light" : "dark")}
    >
      <Icon className="size-4" aria-hidden="true" />
    </Button>
  );
}
