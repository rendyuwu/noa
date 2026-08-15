'use client'

import { useForm, type UseFormReturn } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Input,
  RadioGroup,
  RadioItem,
  Textarea,
  bigsuToast,
} from '@gio/bigsu-ui'

import type { MutationResult } from '@/lib/admin/pmg/use-pmg-servers'
import type { PmgServer } from '@/lib/admin/pmg/types'
import {
  EMPTY_PMG_FORM,
  buildPmgCreatePayload,
  buildPmgUpdatePayload,
  pmgFormStateFromServer,
  type PmgServerFormState,
} from '@/lib/admin/pmg/pmg-form'
import { buildPmgServerFormSchema } from '@/lib/admin/pmg/pmg-schema'

export type ServerFormDialogProps = {
  open: boolean
  mode: 'create' | 'update'
  existingServer: PmgServer | null
  onOpenChangeAction: (open: boolean) => void
  onSubmitAction: (body: Record<string, unknown>) => Promise<MutationResult>
}

// Create/edit PMG server dialog (issue #110). BIGSU form stack: react-hook-form
// + zodResolver, every field wrapped in FormField with a visible label and error
// wiring. The form is keyed by `open`+server id at the parent so each opening
// re-seeds from the server and every secret field starts blank. Secrets are
// write-only: never seeded, cleared on unmount by the re-key, and never retained
// after submit (the dialog closes on success; on failure the non-secret input is
// preserved and the stable backend detail is surfaced). PMG is SSH-only, so SSH
// credentials are mandatory — there is no "enable SSH" toggle.
export function ServerFormDialog({ open, mode, existingServer, ...rest }: ServerFormDialogProps) {
  return (
    <Dialog open={open} onOpenChange={rest.onOpenChangeAction}>
      {open ? (
        <ServerForm
          key={`${mode}:${existingServer?.id ?? 'new'}`}
          mode={mode}
          existingServer={existingServer}
          {...rest}
        />
      ) : null}
    </Dialog>
  )
}

function ServerForm({
  mode,
  existingServer,
  onOpenChangeAction,
  onSubmitAction,
}: Omit<ServerFormDialogProps, 'open'>) {
  const form = useForm<PmgServerFormState>({
    resolver: zodResolver(buildPmgServerFormSchema(mode, existingServer)),
    defaultValues: existingServer ? pmgFormStateFromServer(existingServer) : EMPTY_PMG_FORM,
  })
  const isCreate = mode === 'create'

  const submit = form.handleSubmit(async (values) => {
    const body =
      isCreate || !existingServer
        ? buildPmgCreatePayload(values)
        : buildPmgUpdatePayload(values, existingServer)
    const result = await onSubmitAction(body)
    if (result.ok) {
      bigsuToast.success(isCreate ? 'PMG server added' : 'PMG server saved', {
        description: values.name.trim(),
      })
      onOpenChangeAction(false)
      return
    }
    if (result.message) {
      form.setError('name', { type: 'server', message: result.message })
      bigsuToast.danger(isCreate ? 'Could not add server' : 'Could not save server', {
        description: result.message,
      })
    }
  })

  const busy = form.formState.isSubmitting

  return (
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{isCreate ? 'Add PMG server' : 'Edit PMG server'}</DialogTitle>
        <DialogDescription>
          {isCreate
            ? 'PMG SSH credentials are stored encrypted and never displayed again. PMG validation and whitelist tools use the SSH path.'
            : 'Stored secrets can be replaced, but they are never shown again. Leave a secret blank to keep the stored value.'}
        </DialogDescription>
      </DialogHeader>

      <form id="pmg-server-form" onSubmit={submit} className="flex flex-col gap-5">
        <ServerHostFields form={form} busy={busy} />
        <ServerSshFields form={form} mode={mode} existingServer={existingServer} busy={busy} />
      </form>

      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChangeAction(false)} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" form="pmg-server-form" loading={busy}>
          {isCreate ? 'Save' : 'Save changes'}
        </Button>
      </DialogFooter>
    </DialogContent>
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
