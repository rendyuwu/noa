"use client";

import type { PropsWithChildren } from "react";
import { useId, useState } from "react";

import { ChevronRightIcon } from "@radix-ui/react-icons";

export function ThinkingBlock({ children }: PropsWithChildren) {
  const [open, setOpen] = useState(false);
  const baseId = useId();
  const toggleId = `${baseId}-thinking-toggle`;
  const panelId = `${baseId}-thinking-panel`;

  return (
    <section className="border-l border-border/60 pl-3">
      <button
        type="button"
        id={toggleId}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <span>Thinking</span>
        <ChevronRightIcon
          width={14}
          height={14}
          className={[
            "transition-transform duration-200 motion-reduce:transition-none",
            open ? "rotate-90" : "rotate-0",
          ].join(" ")}
          aria-hidden="true"
        />
      </button>

      <section
        id={panelId}
        aria-labelledby={toggleId}
        hidden={!open}
        className="pt-2"
      >
        <div className="space-y-2 text-sm text-muted-foreground">{children}</div>
      </section>
    </section>
  );
}
