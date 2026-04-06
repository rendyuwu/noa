"use client";

import { useState } from "react";

import type {
  AssistantDetailEvidenceItem,
  AssistantDetailEvidenceSection,
} from "@/components/assistant/approval-state";
import { DisclosureSection, TruncatedText } from "@/components/assistant/inline-disclosure";

type DetailVariant = "sheet" | "inline";
type DetailOpenMode = "default" | "export";

function normalizeTitle(value: string): string {
  return value.trim().toLowerCase();
}

function isNoisySectionTitle(title: string): boolean {
  const normalized = normalizeTitle(title);
  return (
    normalized.includes("log") ||
    normalized.includes("raw") ||
    normalized.includes("json")
  );
}

function shouldSectionStartOpen(title: string): boolean {
  const normalized = normalizeTitle(title);
  if (isNoisySectionTitle(title)) return false;
  if (normalized.includes("overview")) return true;
  if (normalized.includes("before")) return true;
  if (normalized.includes("requested")) return true;
  if (normalized.includes("after")) return true;
  if (normalized.includes("verification")) return true;
  return false;
}

function InlineEvidenceItem({
  item,
  mono,
}: {
  item: AssistantDetailEvidenceItem;
  mono: boolean;
}) {
  return (
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-3">
      <dt className="text-muted">{item.label}</dt>
      <dd className="min-w-0 break-words text-text">
        <TruncatedText text={item.value} initialLines={12} mono={mono} />
      </dd>
    </div>
  );
}

function InlineEvidenceSectionBody({ section }: { section: AssistantDetailEvidenceSection }) {
  const [showAllItems, setShowAllItems] = useState(false);
  const mono = isNoisySectionTitle(section.title);
  const initialCount = normalizeTitle(section.title).includes("overview") ? 999 : 6;
  const items = showAllItems ? section.items : section.items.slice(0, initialCount);
  const hiddenCount = Math.max(0, section.items.length - items.length);

  return (
    <div className="space-y-2">
      <dl className="space-y-2 text-sm">
        {items.map((item, index) => (
          <InlineEvidenceItem
            key={`${section.title}-${item.label}-${index}`}
            item={item}
            mono={mono}
          />
        ))}
      </dl>
      {section.items.length > initialCount ? (
        <button
          type="button"
          onClick={() => setShowAllItems((value) => !value)}
          className={[
            "inline-flex items-center text-[11px] font-medium text-muted",
            "transition-colors hover:text-text",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
          ].join(" ")}
        >
          {showAllItems ? "Show less" : `Show ${hiddenCount} more`}
        </button>
      ) : null}
    </div>
  );
}

export function DetailSections({
  sections,
  variant = "sheet",
  showEmptyState = false,
  openMode = "default",
}: {
  sections: AssistantDetailEvidenceSection[];
  variant?: DetailVariant;
  showEmptyState?: boolean;
  openMode?: DetailOpenMode;
}) {
  if (sections.length === 0) {
    if (!showEmptyState) return null;
    return (
      <div className="rounded-xl border border-dashed border-border bg-bg/20 px-4 py-3 text-sm text-muted">
        No structured evidence is available for this request.
      </div>
    );
  }

  if (variant === "inline") {
    return (
      <div className="space-y-3">
        {sections.map((section) => {
          const shouldOpen =
            openMode === "export"
              ? !isNoisySectionTitle(section.title)
              : shouldSectionStartOpen(section.title);
          return (
            <DisclosureSection
              key={section.title}
              title={section.title}
              count={section.items.length}
              defaultOpen={shouldOpen}
            >
              <InlineEvidenceSectionBody section={section} />
            </DisclosureSection>
          );
        })}
      </div>
    );
  }

  return sections.map((section) => (
    <section
      key={section.title}
      className="rounded-xl border border-border bg-bg/35 px-4 py-3"
    >
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
        {section.title}
      </h3>
      <dl className="mt-3 space-y-2 text-sm">
        {section.items.map((item, index) => (
          <div
            key={`${section.title}-${item.label}-${index}`}
            className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3"
          >
            <dt className="text-muted">{item.label}</dt>
            <dd className="min-w-0 break-words text-text">{item.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  ));
}
