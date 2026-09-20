'use client'

import { Badge, StatusChip } from '@gio/bigsu-ui'

import { ServersPage } from '@/components/admin/servers/servers-page'
import type { ServerVertical } from '@/components/admin/servers/types'
import {
  EMPTY_PMG_FORM,
  buildPmgCreatePayload,
  buildPmgUpdatePayload,
  pmgFormStateFromServer,
  validatePmgServerForm,
  type PmgServerFormState,
} from '@/lib/admin/pmg/pmg-form'
import type { PmgServer, ValidatePmgServerResponse } from '@/lib/admin/pmg/types'
import { usePmgServers } from '@/lib/admin/pmg/use-pmg-servers'
import { formatRelativeTime } from '@/lib/admin/shared/relative-time'
import {
  deriveSshStatus,
  getFingerprintBadge,
  getSshAuthLabel,
} from '@/lib/admin/shared/server-status'

import { PmgServerFields } from './server-form-fields'

// The PMG servers administration page (issue #110), expressed as a config over
// the shared admin servers UI (components/admin/servers). Everything structural
// — header, filter, table, drawer, dialog, race guards — is there; what is here
// is only what PMG does differently.
const PMG_VERTICAL: ServerVertical<PmgServer, ValidatePmgServerResponse, PmgServerFormState> = {
  useServers: usePmgServers,

  title: 'PMG servers',
  description:
    'Manage PMG SSH credentials, validation, and the pinned host key. PMG validation and whitelist tools run over SSH, and secrets are stored encrypted and never displayed after save.',
  searchPlaceholder: 'Search by name, host, or ID',
  emptyTitle: 'No PMG servers yet',
  emptyDescription: 'Add a PMG server to manage mail gateway infrastructure.',
  searchFields: (server) => [server.ssh_host],

  rowSubtitle: (server) => server.ssh_host,
  // PMG is SSH-only, so the pinned host key is its transport-security signal
  // and there is no TLS column to show.
  extraColumns: [
    {
      id: 'ssh',
      header: 'SSH',
      cell: ({ row }) => <StatusChip status={deriveSshStatus(row.original)} />,
    },
    {
      id: 'fingerprint',
      header: 'Host key',
      cell: ({ row }) => {
        const fingerprint = getFingerprintBadge(row.original)
        return <Badge variant={fingerprint.variant}>{fingerprint.label}</Badge>
      },
    },
  ],

  detail: (server, validateResult) => ({
    subtitle: server.ssh_host,
    badges: [getFingerprintBadge(server)],
    rows: [
      { label: 'SSH host/IP', value: server.ssh_host },
      { label: 'Updated', value: formatRelativeTime(server.updated_at, '—') },
      {
        label: 'Latest validation',
        value: validateResult
          ? validateResult.message
          : 'Validate connects over SSH, runs PMG probes, and pins the SSH host key.',
      },
    ],
    sections: [
      {
        title: 'SSH access',
        status: deriveSshStatus(server),
        badges: [getFingerprintBadge(server)],
        rows: [
          { label: 'SSH user', value: server.ssh_username || 'root (default)' },
          { label: 'SSH port', value: server.ssh_port ?? 22 },
          { label: 'Authentication', value: getSshAuthLabel(server) },
          {
            label: 'Host key fingerprint',
            value: (
              <span className="font-mono text-xs">
                {server.ssh_host_key_fingerprint ?? 'Not validated yet'}
              </span>
            ),
          },
        ],
      },
    ],
    deleteDescription: `This permanently deletes the ${server.name} server configuration from NOA, including its stored SSH credentials. This cannot be undone.`,
  }),

  noun: 'PMG server',
  formDescription: (mode) =>
    mode === 'create'
      ? 'PMG SSH credentials are stored encrypted and never displayed again. PMG validation and whitelist tools use the SSH path.'
      : 'Stored secrets can be replaced, but they are never shown again. Leave a secret blank to keep the stored value.',
  formDefaults: (_mode, existingServer) =>
    existingServer ? pmgFormStateFromServer(existingServer) : EMPTY_PMG_FORM,
  validateForm: validatePmgServerForm,
  buildPayload: (values, mode, existingServer) =>
    mode === 'create' || !existingServer
      ? buildPmgCreatePayload(values)
      : buildPmgUpdatePayload(values, existingServer),
  renderFields: ({ form, mode, existingServer, busy }) => (
    <PmgServerFields form={form} mode={mode} existingServer={existingServer} busy={busy} />
  ),
}

export function PmgServersPage() {
  return <ServersPage vertical={PMG_VERTICAL} />
}
