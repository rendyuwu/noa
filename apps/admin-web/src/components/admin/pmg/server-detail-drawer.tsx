'use client'

import {
  Badge,
  Button,
  ConfirmDialog,
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  StatusChip,
} from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import type { MutationResult } from '@/lib/admin/pmg/use-pmg-servers'
import type { PmgServer, ValidatePmgServerResponse } from '@/lib/admin/pmg/types'
import {
  derivePmgSshStatus,
  derivePmgValidationStatus,
  formatPmgRelativeTime,
  getPmgFingerprintBadge,
  getPmgSshAuthLabel,
} from '@/lib/admin/pmg/pmg-status'

export type ServerDetailDrawerProps = {
  server: PmgServer | null
  validateResult: ValidatePmgServerResponse | undefined
  validateBusy: boolean
  deleteBusy: boolean
  onCloseAction: () => void
  onEditAction: () => void
  onValidateAction: (serverId: string) => Promise<MutationResult>
  onDeleteAction: (serverId: string) => Promise<MutationResult>
}

// Contextual detail for one PMG server (issue #110). A Drawer inspects/edits
// alongside the list; a blocking decision (delete) escalates to ConfirmDialog.
// The drawer is controlled — open whenever a server is selected — and its inner
// content is keyed by server id so switching rows re-seeds. No secret is ever
// displayed; only presence (has_ssh_password / has_ssh_private_key) and derived
// state. PMG is SSH-only, so the pinned host-key fingerprint is surfaced
// explicitly and accessibly as its transport-security signal.
export function ServerDetailDrawer({ server, ...rest }: ServerDetailDrawerProps) {
  return (
    <Drawer open={server !== null} onOpenChange={(open) => (open ? undefined : rest.onCloseAction())}>
      {server ? <ServerDetailContent key={server.id} server={server} {...rest} /> : null}
    </Drawer>
  )
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 items-start justify-between gap-4">
      <dt className="shrink-0 text-text-secondary">{label}</dt>
      <dd className="min-w-0 break-all text-right text-text-primary">{value}</dd>
    </div>
  )
}

function ServerDetailContent({
  server,
  validateResult,
  validateBusy,
  deleteBusy,
  onEditAction,
  onValidateAction,
  onDeleteAction,
}: Omit<ServerDetailDrawerProps, 'onCloseAction'> & { server: PmgServer }) {
  const fingerprint = getPmgFingerprintBadge(server)

  const confirmDelete = async () => {
    const result = await onDeleteAction(server.id)
    if (!result.ok) throw new Error(result.message || 'delete failed')
  }

  return (
    <DrawerContent>
      <DrawerHeader>
        <DrawerTitle>{server.name}</DrawerTitle>
        <DrawerDescription>{server.ssh_host}</DrawerDescription>
      </DrawerHeader>

      <div className="flex flex-col gap-6">
        <section className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <StatusChip status={derivePmgValidationStatus(validateResult)} />
            <Badge variant={fingerprint.variant}>{fingerprint.label}</Badge>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              loading={validateBusy}
              disabled={deleteBusy}
              onClick={() => void onValidateAction(server.id)}
            >
              Validate
            </Button>
            <Button variant="outline" disabled={deleteBusy} onClick={onEditAction}>
              <BigsuIcon name="edit" size="sm" aria-hidden />
              Edit
            </Button>
          </div>
        </section>

        <dl className="flex flex-col gap-2 text-sm">
          <DetailRow label="Server ID" value={<span className="font-mono">{server.id}</span>} />
          <DetailRow label="SSH host/IP" value={server.ssh_host} />
          <DetailRow label="Updated" value={formatPmgRelativeTime(server.updated_at)} />
          <DetailRow
            label="Latest validation"
            value={
              validateResult
                ? validateResult.message
                : 'Validate connects over SSH, runs PMG probes, and pins the SSH host key.'
            }
          />
        </dl>

        <section className="flex flex-col gap-3 border-t border-border-default pt-5">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
              SSH access
            </span>
            <div className="flex items-center gap-2">
              <StatusChip status={derivePmgSshStatus(server)} />
              <Badge variant={fingerprint.variant}>{fingerprint.label}</Badge>
            </div>
          </div>
          <dl className="flex flex-col gap-2 text-sm">
            <DetailRow label="SSH user" value={server.ssh_username || 'root (default)'} />
            <DetailRow label="SSH port" value={server.ssh_port ?? 22} />
            <DetailRow label="Authentication" value={getPmgSshAuthLabel(server)} />
            <DetailRow
              label="Host key fingerprint"
              value={
                <span className="font-mono text-xs">
                  {server.ssh_host_key_fingerprint ?? 'Not validated yet'}
                </span>
              }
            />
          </dl>
        </section>
      </div>

      <DrawerFooter>
        <ConfirmDialog
          tone="danger"
          title="Delete server?"
          description={`This permanently deletes the ${server.name} server configuration from NOA, including its stored SSH credentials. This cannot be undone.`}
          confirmLabel="Delete server"
          onConfirm={confirmDelete}
          trigger={
            <Button variant="destructive" disabled={validateBusy || deleteBusy}>
              Delete server
            </Button>
          }
        />
      </DrawerFooter>
    </DrawerContent>
  )
}
