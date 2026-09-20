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

import { deriveServerValidationStatus } from '@/lib/admin/shared/server-status'
import type {
  MutationResult,
  ValidateResultLike,
} from '@/lib/admin/shared/servers-controller-types'

import type { AdminServer, DetailRowSpec, ServerDetailView } from './types'

export type ServerDetailDrawerProps<TServer extends AdminServer, TValidate> = {
  server: TServer | null
  validateResult: TValidate | undefined
  validateBusy: boolean
  deleteBusy: boolean
  onCloseAction: () => void
  onEditAction: () => void
  onValidateAction: (serverId: string) => Promise<MutationResult>
  onDeleteAction: (serverId: string) => Promise<MutationResult>
  detail: (server: TServer, validateResult: TValidate | undefined) => ServerDetailView
}

// Contextual detail for one admin server, whatever the vertical. A Drawer
// inspects/edits alongside the list; a blocking decision (delete) escalates to
// ConfirmDialog. The drawer is controlled — open whenever a server is selected —
// and its inner content is keyed by server id so switching rows re-mounts it
// from scratch. No secret is ever displayed; only presence/derived state.
export function ServerDetailDrawer<TServer extends AdminServer, TValidate extends ValidateResultLike>({
  server,
  ...rest
}: ServerDetailDrawerProps<TServer, TValidate>) {
  return (
    <Drawer open={server !== null} onOpenChange={(open) => (open ? undefined : rest.onCloseAction())}>
      {server ? <ServerDetailContent key={server.id} server={server} {...rest} /> : null}
    </Drawer>
  )
}

// break-all, not break-words: a value here can be an unbroken monospace run —
// an SSH host-key fingerprint, a base_url — that must wrap inside the drawer
// rather than push its width.
function DetailRow({ label, value }: DetailRowSpec) {
  return (
    <div className="flex min-w-0 items-start justify-between gap-4">
      <dt className="shrink-0 text-text-secondary">{label}</dt>
      <dd className="min-w-0 break-all text-right text-text-primary">{value}</dd>
    </div>
  )
}

function ServerDetailContent<TServer extends AdminServer, TValidate extends ValidateResultLike>({
  server,
  validateResult,
  validateBusy,
  deleteBusy,
  onEditAction,
  onValidateAction,
  onDeleteAction,
  detail,
}: Omit<ServerDetailDrawerProps<TServer, TValidate>, 'onCloseAction' | 'server'> & {
  server: TServer
}) {
  const view = detail(server, validateResult)

  const confirmDelete = async () => {
    const result = await onDeleteAction(server.id)
    if (!result.ok) throw new Error(result.message || 'delete failed')
  }

  return (
    <DrawerContent>
      <DrawerHeader>
        <DrawerTitle>{server.name}</DrawerTitle>
        <DrawerDescription>{view.subtitle}</DrawerDescription>
      </DrawerHeader>

      <div className="flex flex-col gap-6">
        <section className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <StatusChip status={deriveServerValidationStatus(validateResult)} />
            {view.badges.map((badge) => (
              <Badge key={badge.label} variant={badge.variant}>
                {badge.label}
              </Badge>
            ))}
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
          {view.rows.map((row) => (
            <DetailRow key={row.label} label={row.label} value={row.value} />
          ))}
        </dl>

        {view.sections.map((section) => (
          <section
            key={section.title}
            className="flex flex-col gap-3 border-t border-border-default pt-5"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
                {section.title}
              </span>
              <div className="flex items-center gap-2">
                {section.status ? <StatusChip status={section.status} /> : null}
                {section.badges.map((badge) => (
                  <Badge key={badge.label} variant={badge.variant}>
                    {badge.label}
                  </Badge>
                ))}
              </div>
            </div>
            <dl className="flex flex-col gap-2 text-sm">
              {section.rows.map((row) => (
                <DetailRow key={row.label} label={row.label} value={row.value} />
              ))}
            </dl>
          </section>
        ))}
      </div>

      <DrawerFooter>
        <ConfirmDialog
          tone="danger"
          title="Delete server?"
          description={view.deleteDescription}
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
