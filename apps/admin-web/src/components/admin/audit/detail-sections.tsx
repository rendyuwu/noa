'use client'

// Evidence / detail rendering for workflow surfaces (issue #105). Rendered INLINE
// inside the message flow — never inside the AppShell right detail panel — so the
// information stays reachable at every breakpoint, including below the width where
// BIGSU hides that panel. Values arrive already redacted from the data layer; this
// module is presentation only. BIGSU has no accordion primitive, so disclosure is
// a composed, fully-labelled native pattern (Button toggle + aria-controls region),
// not an invented component.

import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import { Button } from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import type { ReceiptEvidenceSection } from '@/lib/admin/audit/receipt-evidence'

export function Disclosure({
  title,
  count,
  defaultOpen = false,
  children,
}: {
  title: string
  count?: number
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  const baseId = useId()
  const toggleId = `${baseId}-toggle`
  const panelId = `${baseId}-panel`

  return (
    <div className="rounded-lg border border-border-default bg-surface">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        id={toggleId}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-text-primary">
          <span className="truncate">{title}</span>
          {typeof count === 'number' ? (
            <span className="text-xs text-text-secondary">({count})</span>
          ) : null}
        </span>
        <BigsuIcon
          name={open ? 'chevronDown' : 'chevronRight'}
          size="sm"
          aria-hidden="true"
        />
      </Button>
      <section id={panelId} aria-labelledby={toggleId} hidden={!open} className="px-3 pb-3">
        {open ? children : null}
      </section>
    </div>
  )
}

function EvidenceItemRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-1 gap-1 sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-3">
      <dt className="text-text-secondary">{label}</dt>
      <dd className="min-w-0 break-words text-text-primary">{value}</dd>
    </div>
  )
}

function shouldStartOpen(title: string): boolean {
  const normalized = title.trim().toLowerCase()
  if (normalized.includes('log') || normalized.includes('raw') || normalized.includes('json')) {
    return false
  }
  return (
    normalized.includes('overview') ||
    normalized.includes('requested') ||
    normalized.includes('before') ||
    normalized.includes('after') ||
    normalized.includes('verification')
  )
}

export function DetailSections({
  sections,
  showEmptyState = false,
}: {
  sections: ReceiptEvidenceSection[]
  showEmptyState?: boolean
}) {
  if (sections.length === 0) {
    if (!showEmptyState) return null
    return (
      <div className="rounded-lg border border-dashed border-border-default bg-surface px-4 py-3 text-sm text-text-secondary">
        No structured evidence is available for this request.
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {sections.map((section, index) => (
        <Disclosure
          key={`${section.title}-${index}`}
          title={section.title}
          count={section.items.length}
          defaultOpen={shouldStartOpen(section.title)}
        >
          <dl className="space-y-2 pt-1 text-sm">
            {section.items.map((item, itemIndex) => (
              <EvidenceItemRow
                key={`${section.title}-${item.label}-${itemIndex}`}
                label={item.label}
                value={item.value}
              />
            ))}
          </dl>
        </Disclosure>
      ))}
    </div>
  )
}
