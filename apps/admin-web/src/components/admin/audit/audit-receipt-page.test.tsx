import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => ({
  fetchReceiptPayload: vi.fn(),
}))

vi.mock('@/lib/admin/audit/audit-api', () => ({
  fetchReceiptPayload: mocks.fetchReceiptPayload,
}))

import { AuditReceiptPage } from './audit-receipt-page'

// A receipt payload carrying one benign evidence item and one secret-bearing
// item. The API redacts server-side; the shared workflow-receipt data layer
// redacts again on parse, so a secret must never reach the DOM or any export.
const receiptPayload = {
  replyTemplate: {
    title: 'WHM account created',
    summary: 'Account provisioned successfully.',
    outcome: 'changed',
    nextStep: null,
  },
  actionRequestId: 'req-1',
  threadId: 'thread-1',
  evidenceSections: [
    {
      title: 'Result',
      items: [
        { label: 'Username', value: 'acme_admin' },
        { label: 'Password', value: 'hunter2-should-be-hidden' },
      ],
    },
  ],
}

beforeEach(() => {
  mocks.fetchReceiptPayload.mockReset()
})

describe('AuditReceiptPage', () => {
  it('shows a loading placeholder before the receipt resolves', () => {
    mocks.fetchReceiptPayload.mockReturnValue(new Promise(() => {}))
    render(<AuditReceiptPage actionRequestId="req-1" />)
    expect(screen.getByText('Loading receipt…')).toBeInTheDocument()
  })

  it('renders the receipt with a StatusChip and redacts the secret from the DOM', async () => {
    mocks.fetchReceiptPayload.mockResolvedValue(receiptPayload)
    render(<AuditReceiptPage actionRequestId="req-1" />)

    await waitFor(() => expect(screen.getByText('WHM account created')).toBeInTheDocument())
    // Completed maps to a StatusChip (workflow status).
    expect(screen.getByText('Completed')).toBeInTheDocument()
    // The secret value must never reach the DOM, whether the section is open or not.
    expect(screen.queryByText('hunter2-should-be-hidden')).not.toBeInTheDocument()

    // Expand the "Result" evidence section (collapsed by default) and confirm the
    // benign value shows while the secret stays redacted to its placeholder.
    fireEvent.click(screen.getByRole('button', { name: /result/i }))
    expect(screen.getByText('acme_admin')).toBeInTheDocument()
    expect(screen.queryByText('hunter2-should-be-hidden')).not.toBeInTheDocument()
    expect(screen.getByText('[redacted]')).toBeInTheDocument()
  })

  it('exposes copy/download export controls once the receipt loads', async () => {
    mocks.fetchReceiptPayload.mockResolvedValue(receiptPayload)
    render(<AuditReceiptPage actionRequestId="req-1" />)
    await waitFor(() => expect(screen.getByText('WHM account created')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save image' })).toBeInTheDocument()
  })

  it('shows an actionable error when the receipt fails to load', async () => {
    mocks.fetchReceiptPayload.mockRejectedValue(new Error('nope'))
    render(<AuditReceiptPage actionRequestId="req-1" />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument(),
    )
  })
})
