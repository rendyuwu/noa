"use client";

import { Fragment, useId, useMemo, useState } from "react";

import { ChevronRightIcon } from "@radix-ui/react-icons";
import type { ReactNode } from "react";

function clampStyle(lines: number) {
  return {
    display: "-webkit-box",
    WebkitBoxOrient: "vertical" as const,
    WebkitLineClamp: lines,
    overflow: "hidden",
  };
}

export function DisclosureSection({
  title,
  icon,
  meta,
  count,
  defaultOpen = false,
  children,
}: {
  title: string;
  icon?: ReactNode;
  meta?: string;
  count?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const baseId = useId();
  const toggleId = `${baseId}-toggle`;
  const panelId = `${baseId}-panel`;

  return (
    <section className="rounded-xl border border-border bg-bg/35 px-4 py-3">
      <button
        type="button"
        id={toggleId}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className={[
          "flex w-full items-center justify-between gap-3 text-left",
          "text-[11px] font-semibold uppercase tracking-[0.08em] text-muted",
          "transition-colors hover:text-text",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        ].join(" ")}
      >
        <span className="flex min-w-0 items-center gap-2">
          {icon ? <span className="text-muted">{icon}</span> : null}
          <span className="min-w-0 truncate">{title}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {meta ? (
            <span className="rounded-full bg-bg/40 px-2 py-0.5 font-ui text-[11px] text-muted">
              {meta}
            </span>
          ) : null}
          {typeof count === "number" ? (
            <span className="rounded-full bg-bg/40 px-2 py-0.5 font-ui text-[11px] text-muted">
              {count}
            </span>
          ) : null}
          <ChevronRightIcon
            width={16}
            height={16}
            className={[
              "text-muted transition-transform duration-200 motion-reduce:transition-none",
              open ? "rotate-90" : "rotate-0",
            ].join(" ")}
            aria-hidden="true"
          />
        </span>
      </button>
      <div
        id={panelId}
        role="region"
        aria-labelledby={toggleId}
        hidden={!open}
        className="mt-3"
      >
        {children}
      </div>
    </section>
  );
}

function getLineCount(value: string): number {
  if (!value) return 0;
  return value.split(/\r?\n/).length;
}

export function TruncatedText({
  text,
  initialLines = 12,
  mono = false,
}: {
  text: string;
  initialLines?: number;
  mono?: boolean;
}) {
  const normalized = text.trim();
  const shouldOfferToggle = useMemo(() => {
    const lineCount = getLineCount(normalized);
    if (lineCount > initialLines) return true;
    return normalized.length > 320;
  }, [initialLines, normalized]);
  const [expanded, setExpanded] = useState(false);

  if (!shouldOfferToggle) {
    return (
      <span
        className={["whitespace-pre-wrap break-words", mono ? "font-mono text-[12px]" : ""].join(
          " ",
        )}
      >
        {text}
      </span>
    );
  }

  return (
    <div className="space-y-1">
      <div
        className={["whitespace-pre-wrap break-words", mono ? "font-mono text-[12px]" : ""].join(
          " ",
        )}
        style={expanded ? undefined : clampStyle(initialLines)}
      >
        {text}
      </div>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className={[
          "inline-flex items-center text-[11px] font-medium text-muted",
          "transition-colors hover:text-text",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        ].join(" ")}
      >
        {expanded ? "Show less" : "Show full"}
      </button>
    </div>
  );
}

export function TruncatedItemList<T>({
  items,
  initialCount = 6,
  getKey,
  renderItem,
}: {
  items: T[];
  initialCount?: number;
  getKey?: (item: T, index: number) => string;
  renderItem: (item: T, index: number) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const visibleItems = expanded ? items : items.slice(0, initialCount);
  const hiddenCount = Math.max(0, items.length - visibleItems.length);

  return (
    <div className="space-y-2">
      <div className="space-y-2">
        {visibleItems.map((item, index) => (
          <Fragment key={getKey ? getKey(item, index) : String(index)}>
            {renderItem(item, index)}
          </Fragment>
        ))}
      </div>
      {items.length > initialCount ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className={[
            "inline-flex items-center text-[11px] font-medium text-muted",
            "transition-colors hover:text-text",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
          ].join(" ")}
        >
          {expanded ? "Show less" : `Show ${hiddenCount} more`}
        </button>
      ) : null}
    </div>
  );
}
