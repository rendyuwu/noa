"use client";

import type { ReactNode } from "react";
import { ChainOfThoughtPrimitive } from "@assistant-ui/react";
import { ChevronDown } from "lucide-react";

import { ToolFallback } from "./assistant-tool-ui";

function Reasoning({ children, text }: { children?: ReactNode; text?: string }) {
  return <p className="px-4 py-3 font-ui text-sm italic text-muted">{text ?? children}</p>;
}

function Layout({ children }: { children?: ReactNode }) {
  return <div className="border-t border-border/60">{children}</div>;
}

export function AssistantChainOfThought() {
  return (
    <ChainOfThoughtPrimitive.Root className="mt-3 overflow-hidden rounded-2xl border border-border bg-surface">
      <ChainOfThoughtPrimitive.AccordionTrigger className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-surface-2/70">
        <ChevronDown className="size-4 shrink-0 text-muted transition-transform duration-200 ease-out" />
        <span className="text-sm font-semibold text-text">Thinking</span>
      </ChainOfThoughtPrimitive.AccordionTrigger>

      <div className="grid overflow-hidden transition-[grid-template-rows,opacity] duration-200 ease-out grid-rows-[1fr] opacity-100 pointer-events-auto">
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
