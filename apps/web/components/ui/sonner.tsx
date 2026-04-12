"use client";

import type { ComponentProps } from "react";

import { useTheme } from "next-themes";
import { Toaster as Sonner } from "sonner";

type ToasterProps = ComponentProps<typeof Sonner>;

export function Toaster({ ...props }: ToasterProps) {
  const { theme = "system" } = useTheme();

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast rounded-2xl border border-border bg-card/95 text-card-foreground shadow-[0_12px_30px_-18px_rgba(15,23,42,0.35)] group-[.toaster]:bg-card/95 group-[.toaster]:text-card-foreground group-[.toaster]:border-border group-[.toaster]:shadow-[0_12px_30px_-18px_rgba(15,23,42,0.35)]",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:rounded-full group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:rounded-full group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  );
}
