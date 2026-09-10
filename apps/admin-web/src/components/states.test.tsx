import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import {
  ForbiddenView,
  IncidentId,
  NotFoundView,
  PendingApprovalView,
  UnexpectedErrorView,
} from '@/components/states'

describe('shared application states', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  // The escape is labelled "Back to home" since the role-aware dispatcher: it leads to `/home`, which
  // dispatches by role, so wording that named the admin surface was wrong for
  // exactly the operator most likely to be reading a 403.
  it('403 explains the refusal and offers a route home (never a dead end)', () => {
    render(<ForbiddenView />)
    expect(screen.getByRole('heading', { name: /don’t have access/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /back to home/i })).toBeInTheDocument()
  })

  it('404 offers a route home', () => {
    render(<NotFoundView />)
    expect(screen.getByRole('heading', { name: /page not found/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /back to home/i })).toBeInTheDocument()
  })

  it('pending-approval is a distinct state with a sign-out escape', () => {
    const onSignOut = vi.fn()
    render(<PendingApprovalView onSignOut={onSignOut} />)
    expect(screen.getByRole('heading', { name: /pending approval/i })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /sign out/i }))
    expect(onSignOut).toHaveBeenCalledOnce()
  })

  it('unexpected-error offers inline retry (actionable, not toast-only)', () => {
    const onRetry = vi.fn()
    render(<UnexpectedErrorView onRetry={onRetry} />)
    expect(screen.getByRole('heading', { name: /something went wrong/i })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('surfaces the incident id in a copyable monospace element when available', () => {
    render(<UnexpectedErrorView incidentId="req_abc123" onRetry={vi.fn()} />)
    const id = screen.getByTestId('incident-id')
    expect(id).toHaveTextContent('req_abc123')
    expect(id.className).toContain('font-mono')
  })

  it('omits the incident id block when none is available', () => {
    render(<UnexpectedErrorView onRetry={vi.fn()} />)
    expect(screen.queryByTestId('incident-id')).not.toBeInTheDocument()
  })

  it('copies the incident id to the clipboard on demand', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(<IncidentId id="req_xyz" />)
    fireEvent.click(screen.getByRole('button', { name: /copy/i }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('req_xyz'))
  })
})
