'use client'

import {
  useForm,
  type DefaultValues,
  type FieldErrors,
  type FieldValues,
  type Path,
  type Resolver,
} from 'react-hook-form'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  bigsuToast,
} from '@gio/bigsu-ui'

import type { MutationResult } from '@/lib/admin/shared/servers-controller-types'
import { submitOnEnter } from '@/lib/forms/submit-on-enter'

import type { AdminServer, ServerFormSpec } from './types'

export type ServerFormDialogProps<
  TServer extends AdminServer,
  TForm extends FieldValues & { name: string },
> = {
  spec: ServerFormSpec<TServer, TForm>
  open: boolean
  mode: 'create' | 'update'
  existingServer: TServer | null
  onOpenChangeAction: (open: boolean) => void
  onSubmitAction: (body: Record<string, unknown>) => Promise<MutationResult>
}

// Create/edit dialog shell for every admin server vertical. BIGSU form stack:
// react-hook-form, every field wrapped in FormField with a visible label and
// error wiring. The inner form is keyed by mode+server id so each opening
// re-seeds from the server and every secret field starts blank. Secrets are
// write-only: never seeded, cleared on unmount by the re-key (covering close and
// cancel), and never retained after submit (the dialog closes on success; on
// failure the non-secret input is preserved and the stable backend detail is
// surfaced). Because the whole ServerForm unmounts whenever `open` is false, no
// secret ever survives a close/cancel/failure-then-close in component state.
export function ServerFormDialog<
  TServer extends AdminServer,
  TForm extends FieldValues & { name: string },
>({ open, mode, existingServer, ...rest }: ServerFormDialogProps<TServer, TForm>) {
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

function ServerForm<TServer extends AdminServer, TForm extends FieldValues & { name: string }>({
  spec,
  mode,
  existingServer,
  onOpenChangeAction,
  onSubmitAction,
}: Omit<ServerFormDialogProps<TServer, TForm>, 'open'>) {
  const isCreate = mode === 'create'

  // The vertical's validateForm is the single source of the form's cross-field
  // rules; this resolver only routes its first failure to that field, so
  // FormField renders per-field error text. The `ref` is what a resolver is
  // contracted to populate (zodResolver did, via the same options.fields) and
  // is kept for that reason alone — MEASURED: deleting it changes nothing
  // observable here, because react-hook-form's shouldFocusError walks its own
  // _fields and only asks whether an error exists at that key. What IS
  // load-bearing is the errors branch itself: drop it and the mismatch message
  // never reaches a FormField.
  const resolver: Resolver<TForm> = (values, _context, options) => {
    const error = spec.validateForm(values, mode, existingServer)
    if (!error) return { values, errors: {} }
    return {
      values: {},
      errors: {
        [error.field]: {
          type: 'custom',
          message: error.message,
          ref: options.fields[error.field]?.ref,
        },
      } as FieldErrors<TForm>,
    }
  }

  // The third generic pins TTransformedValues to TForm; left to its default it
  // resolves to an unrelated type parameter and handleSubmit stops matching.
  const form = useForm<TForm, unknown, TForm>({
    resolver,
    defaultValues: spec.formDefaults(mode, existingServer) as DefaultValues<TForm>,
  })

  const submit = form.handleSubmit(async (values) => {
    const body = spec.buildPayload(values, mode, existingServer)
    const result = await onSubmitAction(body)
    spec.afterSubmit?.(form)
    if (result.ok) {
      bigsuToast.success(`${spec.noun} ${isCreate ? 'added' : 'saved'}`, {
        description: values.name.trim(),
      })
      onOpenChangeAction(false)
      return
    }
    if (result.message) {
      form.setError('name' as Path<TForm>, { type: 'server', message: result.message })
      bigsuToast.danger(isCreate ? 'Could not add server' : 'Could not save server', {
        description: result.message,
      })
    }
  })

  const busy = form.formState.isSubmitting

  return (
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{`${isCreate ? 'Add' : 'Edit'} ${spec.noun}`}</DialogTitle>
        <DialogDescription>{spec.formDescription(mode)}</DialogDescription>
      </DialogHeader>

      <form onSubmit={submit} onKeyDown={submitOnEnter(submit)} className="flex flex-col gap-5">
        {spec.renderFields({ form, mode, existingServer, busy })}
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
