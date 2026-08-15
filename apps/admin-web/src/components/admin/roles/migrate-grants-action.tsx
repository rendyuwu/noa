'use client'

import { useState } from 'react'
import { Button, ConfirmDialog, bigsuToast } from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import { toMessage } from '@/lib/admin/shared/error-message'
import { migrateDirectGrants } from '@/lib/admin/roles/roles-api'
import { summarizeDirectGrantsMigration } from '@/lib/admin/roles/migration-summary'

export type MigrateGrantsActionProps = {
  disabled?: boolean
  onMigrated: () => void
}

// Legacy direct-grant migration (issue #107). The action is destructive-adjacent
// (it rewrites per-user grants into role grants), so it is gated behind a
// ConfirmDialog whose description states the consequence; it is safe to run
// repeatedly. On success the normalised summary is announced through a polite
// live region so the operator sees exactly what changed, and the page reloads to
// pick up any roles the migration created. ConfirmDialog stays open if the
// migration rejects, preserving the decision and the stable backend detail.
export function MigrateGrantsAction({ disabled, onMigrated }: MigrateGrantsActionProps) {
  const [summary, setSummary] = useState<string | null>(null)

  const confirmMigrate = async () => {
    try {
      const payload = await migrateDirectGrants()
      const text = summarizeDirectGrantsMigration(payload)
      setSummary(text)
      bigsuToast.success('Migration complete', { description: text })
      onMigrated()
    } catch (error) {
      const message = toMessage(error, 'Unable to migrate legacy direct grants')
      bigsuToast.danger('Could not migrate direct grants', { description: message })
      throw new Error(message)
    }
  }

  return (
    <>
      <ConfirmDialog
        title="Migrate legacy direct grants?"
        description="Convert any remaining per-user direct tool grants into role-based grants. This is safe to run multiple times."
        confirmLabel="Migrate grants"
        onConfirm={confirmMigrate}
        trigger={
          <Button variant="outline" disabled={disabled}>
            <BigsuIcon name="refresh" size="sm" aria-hidden />
            Migrate direct grants
          </Button>
        }
      />
      {summary ? (
        <p role="status" aria-live="polite" className="sr-only">
          {summary}
        </p>
      ) : null}
    </>
  )
}
