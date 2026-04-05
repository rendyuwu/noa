"use client";

import type { ReactNode } from "react";
import { ChainOfThoughtPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import { ChevronDown } from "lucide-react";

import { ToolFallback } from "./assistant-tool-ui";

function Reasoning({ children, text }: { children?: ReactNode; text?: string }) {
  return <p className="px-4 py-3 font-ui text-sm italic text-muted">{text ?? children}</p>;
}

function Layout({ children }: { children?: ReactNode }) {
  return <div className="border-t border-border/60">{children}</div>;
}

export function AssistantChainOfThought() {
  const aui = useAui();
  const hasChainOfThought = Boolean(aui.chainOfThought.source);
  const isCollapsed = useAuiState((state) => (hasChainOfThought ? state.chainOfThought.collapsed : true));

  if (!hasChainOfThought) {
    return null;
  }

  return (
    <ChainOfThoughtPrimitive.Root className="mt-3 overflow-hidden rounded-2xl border border-border bg-surface">
      <ChainOfThoughtPrimitive.AccordionTrigger
        aria-expanded={!isCollapsed}
        className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-surface-2/70"
      >
        <ChevronDown
          className={[
            "size-4 shrink-0 text-muted transition-transform duration-200 ease-out",
            isCollapsed ? "rotate-0" : "rotate-180",
          ].join(" ")}
        />
        <span className="text-sm font-semibold text-text">Thinking</span>
      </ChainOfThoughtPrimitive.AccordionTrigger>

      <div
        aria-hidden={isCollapsed}
        className={[
          "grid overflow-hidden transition-[grid-template-rows,opacity] duration-200 ease-out",
          isCollapsed ? "grid-rows-[0fr] opacity-0" : "grid-rows-[1fr] opacity-100",
          isCollapsed ? "pointer-events-none" : "pointer-events-auto",
        ].join(" ")}
      >
        <div className="overflow-hidden">
          <ChainOfThoughtPrimitive.Parts
            components={{
              Reasoning,
              tools: { Fallback: ToolFallback },
              Layout,
            }}
          />
        </div>
      </div>
    </ChainOfThoughtPrimitive.Root>
  );
}
