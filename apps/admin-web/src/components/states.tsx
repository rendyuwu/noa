'use client'

import type { ReactNode } from 'react'
import { BigsuIcon } from '@gio/bigsu-icons'
import type { BigsuIconName } from '@gio/bigsu-icons'
import { Button, ErrorState, LoadingSkeleton, bigsuToast } from '@gio/bigsu-ui'

// Shared BIGSU application states. Every protected surface routes through these
// so no path dead-ends: loading, pending-approval, forbidden (403), not-found
// (404), and unexpected-error. Actionable failures always render an inline
// recovery path here — they are never toast-only.

// Where "home" goes now that administration is the whole app (§I.admin-web).
// The first admin vertical rather than `/admin`, which has no page of its own.
const HOME_HREF = '/admin/users'

// Chrome-less centered shell for the terminal states (rendered before or
// outside the AppShell, so it owns the page `main` landmark).
function StateShell({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-app px-4 py-10">
      <div className="flex w-full max-w-md flex-col items-center gap-4 text-center">{children}</div>
    </main>
  )
}

function StateBadge({
  icon,
  tone,
}: {
  icon: BigsuIconName
  tone: 'warning' | 'muted'
}) {
  const toneClass =
    tone === 'warning'
      ? 'bg-status-warning-soft text-status-warning'
      : 'bg-action-primary-soft text-action-primary'
  return (
    <span className={`flex size-12 items-center justify-center rounded-lg ${toneClass}`}>
      <BigsuIcon name={icon} size="xl" aria-hidden />
    </span>
  )
}

// Recovery action shared by the terminal states so a user is never stranded.
// A full navigation is deliberate: these states render outside (or before) the
// router-driven shell, so a hard link home is the robust escape.
function HomeButton() {
  return (
    <Button variant="secondary" onClick={() => window.location.assign(HOME_HREF)}>
      Back to administration
    </Button>
  )
}

// Pre-shell loading (owns its own `main`): used by the auth gate before chrome
// exists. In-shell page loading uses PageLoadingSkeleton instead.
export function LoadingView({ label = 'Loading' }: { label?: string }) {
  return (
    <main className="flex min-h-dvh flex-col gap-4 bg-app p-8" aria-busy="true" aria-label={label}>
      <LoadingSkeleton className="h-8 w-64" />
      <LoadingSkeleton className="h-40 w-full" />
    </main>
  )
}

// In-shell page loading fallback (no `main`; the AppShell already provides one).
export function PageLoadingSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading page">
      <LoadingSkeleton className="h-8 w-64" />
      <LoadingSkeleton className="h-40 w-full" />
    </div>
  )
}

// Authenticated but inactive/awaiting approval — a distinct product state, not
// an error and not an expiry.
export function PendingApprovalView({ onSignOut }: { onSignOut: () => void }) {
  return (
    <StateShell>
      <StateBadge icon="approvals" tone="warning" />
      <h1 className="text-2xl font-semibold text-text-primary">Account pending approval</h1>
      <p className="text-sm text-text-secondary">
        Your sign-in worked, but your account is awaiting administrator approval. You will get
        access once it is activated.
      </p>
      <Button variant="secondary" onClick={onSignOut}>
        Sign out
      </Button>
    </StateShell>
  )
}

// 403: verified, but not permitted. Server authorization is the source of truth;
// this only explains the refusal and offers a way back — never a dead end.
export function ForbiddenView() {
  return (
    <StateShell>
      <StateBadge icon="security" tone="warning" />
      <h1 className="text-2xl font-semibold text-text-primary">You don’t have access</h1>
      <p className="text-sm text-text-secondary">
        Your account isn’t permitted to view this page. If you think this is a mistake, contact an
        administrator.
      </p>
      <HomeButton />
    </StateShell>
  )
}

// 404: unknown route. Always offers a route home.
export function NotFoundView() {
  return (
    <StateShell>
      <StateBadge icon="search" tone="muted" />
      <h1 className="text-2xl font-semibold text-text-primary">Page not found</h1>
      <p className="text-sm text-text-secondary">
        We couldn’t find the page you’re looking for. It may have moved, or the link is wrong.
      </p>
      <HomeButton />
    </StateShell>
  )
}

// Copyable, monospace incident/request identifier surfaced with unexpected
// errors so a user can hand it to support (issue #101, criterion 7).
export function IncidentId({ id }: { id: string }) {
  const copy = () => {
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
    if (!clipboard) {
      bigsuToast.warning('Copy isn’t available — please write the incident ID down')
      return
    }
    void clipboard.writeText(id).then(
      () => bigsuToast.success('Incident ID copied'),
      () => bigsuToast.danger('Could not copy the incident ID'),
    )
  }
  return (
    <div className="flex items-center gap-2 rounded-md border border-border-default bg-surface px-3 py-2">
      <span className="text-xs text-text-secondary">Incident ID</span>
      <code className="font-mono text-xs text-text-primary" data-testid="incident-id">
        {id}
      </code>
      <Button variant="ghost" size="sm" onClick={copy}>
        Copy
      </Button>
    </div>
  )
}

// Unexpected transport/render failure. Inline recovery (retry) plus the copyable
// incident ID when one is available — actionable, not toast-only.
export function UnexpectedErrorView({
  incidentId,
  onRetry,
}: {
  incidentId?: string
  onRetry?: () => void
}) {
  return (
    <StateShell>
      <ErrorState
        title="Something went wrong"
        description="An unexpected error interrupted this page. You can retry, and share the incident ID below with support if it keeps happening."
        onRetry={onRetry}
      />
      {incidentId ? <IncidentId id={incidentId} /> : null}
    </StateShell>
  )
}
