'use client'

import type { UseFormReturn } from 'react-hook-form'
import { FormField, Input, RadioGroup, RadioItem, Textarea } from '@gio/bigsu-ui'

import type { PmgServer } from '@/lib/admin/pmg/types'
import type { PmgServerFormState } from '@/lib/admin/pmg/pmg-form'

// The PMG half of the create/edit server dialog (issue #110) — the field
// sections that genuinely differ between verticals. The shell around them
// (dialog, resolver, submit, toasts) is components/admin/servers.
// PMG is SSH-only, so SSH credentials are mandatory — there is no "enable SSH"
// toggle — and the host-key fingerprint is an editable field here.

type FieldsProps = {
  form: UseFormReturn<PmgServerFormState>
  mode: 'create' | 'update'
  existingServer: PmgServer | null
  busy: boolean
}

export function PmgServerFields({ form, mode, existingServer, busy }: FieldsProps) {
  return (
    <>
      <ServerHostFields form={form} busy={busy} />
      <ServerSshFields form={form} mode={mode} existingServer={existingServer} busy={busy} />
    </>
  )
}

function ServerHostFields({
  form,
  busy,
}: {
  form: UseFormReturn<PmgServerFormState>
  busy: boolean
}) {
  const { register, formState } = form
  const errors = formState.errors
  return (
    <section className="flex flex-col gap-4">
      <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
        PMG server
      </span>
      <FormField label="Name" errorText={errors.name?.message}>
        <Input {...register('name')} placeholder="pmg1" disabled={busy} autoComplete="off" />
      </FormField>
      <FormField
        label="SSH host/IP"
        errorText={errors.sshHost?.message}
        helperText="NOA connects to this host over SSH. The PMG web port is not required."
      >
        <Input
          {...register('sshHost')}
          placeholder="pmg.example.com or 203.0.113.10"
          disabled={busy}
          autoComplete="off"
        />
      </FormField>
    </section>
  )
}

function ServerSshFields({
  form,
  mode,
  existingServer,
  busy,
}: {
  form: UseFormReturn<PmgServerFormState>
  mode: 'create' | 'update'
  existingServer: PmgServer | null
  busy: boolean
}) {
  const { register, watch, setValue, formState } = form
  const errors = formState.errors
  const authMode = watch('sshAuthMode')

  return (
    <section className="flex flex-col gap-4 border-t border-border-default pt-5">
      <div className="flex flex-col gap-1">
        <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
          SSH access
        </span>
        <p className="text-sm text-text-secondary">
          {mode === 'update'
            ? 'Leave secret fields blank to keep the stored values. Enter a new value to replace them.'
            : 'Used for PMG validation, whitelist, and other SSH-backed server tools.'}
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="SSH username" helperText="Leave blank to fall back to root.">
          <Input
            {...register('sshUsername')}
            placeholder={existingServer?.ssh_username ?? 'root (default)'}
            disabled={busy}
            autoComplete="off"
          />
        </FormField>
        <FormField
          label="SSH port"
          errorText={errors.sshPort?.message}
          helperText="Leave blank to use port 22."
        >
          <Input
            {...register('sshPort')}
            type="number"
            min={1}
            max={65535}
            placeholder={existingServer?.ssh_port != null ? String(existingServer.ssh_port) : '22'}
            disabled={busy}
          />
        </FormField>
      </div>

      <FormField
        label="SSH host key fingerprint"
        helperText="Validate can refresh this fingerprint after the first SSH connection."
      >
        <Input
          {...register('sshHostKeyFingerprint')}
          placeholder="SHA256:..."
          disabled={busy}
          autoComplete="off"
        />
      </FormField>

      <FormField label="Authentication">
        <RadioGroup
          aria-label="Authentication"
          value={authMode}
          onValueChange={(value) =>
            setValue('sshAuthMode', value === 'password' ? 'password' : 'private_key')
          }
        >
          <RadioItem value="private_key" label="SSH key" disabled={busy} />
          <RadioItem value="password" label="Password" disabled={busy} />
        </RadioGroup>
      </FormField>

      {authMode === 'password' ? (
        <FormField label="SSH password" errorText={errors.sshPassword?.message}>
          <Input
            {...register('sshPassword')}
            type="password"
            placeholder={mode === 'create' ? '••••••••••' : 'Stored — enter a new password to replace'}
            disabled={busy}
            autoComplete="new-password"
          />
        </FormField>
      ) : (
        <>
          <FormField label="SSH private key" errorText={errors.sshPrivateKey?.message}>
            <Textarea
              {...register('sshPrivateKey')}
              className="min-h-32 font-mono text-xs"
              placeholder={
                mode === 'create'
                  ? '-----BEGIN OPENSSH PRIVATE KEY-----'
                  : 'Stored — paste a new private key to replace'
              }
              disabled={busy}
            />
          </FormField>
          <FormField
            label="Key passphrase"
            helperText="Optional. If you replace the private key and leave this blank, the stored passphrase is cleared."
          >
            <Input
              {...register('sshPrivateKeyPassphrase')}
              type="password"
              placeholder={mode === 'create' ? 'Optional' : 'Stored — enter a new passphrase to replace'}
              disabled={busy}
              autoComplete="new-password"
            />
          </FormField>
        </>
      )}
    </section>
  )
}
