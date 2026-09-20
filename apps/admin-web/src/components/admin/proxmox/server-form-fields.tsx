'use client'

import type { UseFormReturn } from 'react-hook-form'
import { Checkbox, FormField, Input } from '@gio/bigsu-ui'

import type { ProxmoxServerFormState } from '@/lib/admin/proxmox/proxmox-form'

// The Proxmox half of the create/edit server dialog (issue #109) — the field
// section that genuinely differs between verticals. The shell around it
// (dialog, resolver, submit, toasts) is components/admin/servers.
// Proxmox authenticates with an API token only; there is no SSH section.

export function ProxmoxServerFields({
  form,
  mode,
  busy,
}: {
  form: UseFormReturn<ProxmoxServerFormState>
  mode: 'create' | 'update'
  busy: boolean
}) {
  const { register, watch, setValue, formState } = form
  const errors = formState.errors
  return (
    <section className="flex flex-col gap-4">
      <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
        Proxmox API
      </span>
      <FormField label="Name" errorText={errors.name?.message}>
        <Input {...register('name')} placeholder="pve1" disabled={busy} autoComplete="off" />
      </FormField>
      <FormField label="Base URL" errorText={errors.baseUrl?.message}>
        <Input
          {...register('baseUrl')}
          placeholder="https://pve.example.com:8006"
          disabled={busy}
          autoComplete="off"
        />
      </FormField>
      <FormField label="API token ID" errorText={errors.apiTokenId?.message}>
        <Input
          {...register('apiTokenId')}
          placeholder="user@pam!tokenname"
          disabled={busy}
          autoComplete="off"
        />
      </FormField>
      <FormField
        label="API token secret"
        errorText={errors.apiTokenSecret?.message}
        helperText={mode === 'update' ? 'Leave blank to keep the stored API token secret.' : undefined}
      >
        <Input
          {...register('apiTokenSecret')}
          type="password"
          placeholder={mode === 'create' ? '••••••••••' : 'Stored — enter a new secret to replace'}
          disabled={busy}
          autoComplete="new-password"
        />
      </FormField>
      <Checkbox
        label="Verify SSL"
        checked={watch('verifySsl')}
        onCheckedChange={(checked) => setValue('verifySsl', checked === true)}
        disabled={busy}
      />
    </section>
  )
}
