'use client'

import { useForm, type UseFormReturn } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Input,
  bigsuToast,
} from '@gio/bigsu-ui'

import type { MutationResult } from '@/lib/admin/proxmox/use-proxmox-servers'
import type { ProxmoxServer } from '@/lib/admin/proxmox/types'
import {
  EMPTY_PROXMOX_FORM,
  buildProxmoxCreatePayload,
  buildProxmoxUpdatePayload,
  proxmoxFormStateFromServer,
  type ProxmoxServerFormState,
} from '@/lib/admin/proxmox/proxmox-form'
import { buildProxmoxServerFormSchema } from '@/lib/admin/proxmox/proxmox-schema'

export type ServerFormDialogProps = {
  open: boolean
  mode: 'create' | 'update'
  existingServer: ProxmoxServer | null
  onOpenChangeAction: (open: boolean) => void
  onSubmitAction: (body: Record<string, unknown>) => Promise<MutationResult>
}

// Create/edit Proxmox server dialog (issue #109). BIGSU form stack: react-hook-
// form + zodResolver, every field wrapped in FormField with a visible label and
// error wiring. The inner form is keyed by `mode`+server id at the parent so each
// opening re-seeds from the server and the secret field starts blank. The secret
// is write-only: never seeded, cleared on unmount by the re-key (covering close
// and cancel), and never retained after submit (the dialog closes on success; on
// failure the non-secret input is preserved and the stable backend detail is
// surfaced). Because the whole ServerForm unmounts whenever `open` is false, no
// secret ever survives a close/cancel/failure-then-close in component state.
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
  const isCreate = mode === 'create'
  const form = useForm<ProxmoxServerFormState>({
    resolver: zodResolver(buildProxmoxServerFormSchema(mode)),
    defaultValues:
      existingServer && !isCreate ? proxmoxFormStateFromServer(existingServer) : EMPTY_PROXMOX_FORM,
  })

  const submit = form.handleSubmit(async (values) => {
    const body =
      isCreate || !existingServer
        ? buildProxmoxCreatePayload(values)
        : buildProxmoxUpdatePayload(values)
    const result = await onSubmitAction(body)
    // Whatever the outcome, the secret must not persist: blank it immediately so
    // it never lingers in form state after a submit (success closes the dialog;
    // a failure keeps the non-secret input for correction but drops the secret).
    form.setValue('apiTokenSecret', '')
    if (result.ok) {
      bigsuToast.success(isCreate ? 'Proxmox server added' : 'Proxmox server saved', {
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
        <DialogTitle>{isCreate ? 'Add Proxmox server' : 'Edit Proxmox server'}</DialogTitle>
        <DialogDescription>
          {isCreate
            ? 'The Proxmox API token secret is stored encrypted and never displayed again.'
            : 'Stored secrets can be replaced, but they are never shown again. Leave the secret blank to keep the stored value.'}
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={submit} className="flex flex-col gap-5">
        <ServerApiFields form={form} mode={mode} busy={busy} />
      </form>

      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChangeAction(false)} disabled={busy}>
          Cancel
        </Button>
        {/* Click handler, not a submit button — sandbox-inheriting tabs refuse form submission. */}
        <Button type="button" onClick={() => void submit()} loading={busy}>
          {isCreate ? 'Save' : 'Save changes'}
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}

function ServerApiFields({
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
