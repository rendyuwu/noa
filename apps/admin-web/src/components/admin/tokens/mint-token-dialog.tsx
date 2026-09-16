'use client'

import { useState } from 'react'
import { useForm } from 'react-hook-form'
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
  bigsuToast,
} from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import {
  MAX_LABEL_LENGTH,
  mintTokenSchema,
  type MintTokenValues,
} from '@/lib/admin/tokens/label-schema'
import type { MintOutcome } from '@/lib/admin/tokens/use-tokens'
import type { MintedToken } from '@/lib/admin/tokens/types'
import { submitOnEnter } from '@/lib/forms/submit-on-enter'

import { MintedTokenPanel } from './minted-token-panel'

export type MintTokenDialogProps = {
  open: boolean
  onOpenChangeAction: (open: boolean) => void
  onMintAction: (label: string | null) => Promise<MintOutcome>
}

// Mint dialog: one controlled Dialog, two steps in the same body. BIGSU
// forbids nesting dialogs, so the minted-token step REPLACES the form step
// rather than opening on top of it.
//
// The plaintext lives in this component's `useState` and nowhere else. The
// controller never receives it (`MintOutcome` hands it back as a return
// value — write-once display, one render site), no toast carries it, no URL
// carries it, and no storage holds it. Two independent things discard it:
//
//  1. `handleOpenChange(false)` nulls the state before the parent is told, so
//     Done, Cancel, Escape and an overlay click all take the same path.
//  2. `{open ? … : null}` unmounts the whole subtree when the dialog closes.
//     Radix `forceMount` is deliberately not used anywhere here, so unmount
//     really discards — re-opening builds a fresh form step with no value.
export function MintTokenDialog({ open, onOpenChangeAction, onMintAction }: MintTokenDialogProps) {
  const [minted, setMinted] = useState<MintedToken | null>(null)

  const handleOpenChange = (next: boolean) => {
    if (!next) setMinted(null)
    onOpenChangeAction(next)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {open ? (
        minted ? (
          <MintedTokenPanel minted={minted} onDoneAction={() => handleOpenChange(false)} />
        ) : (
          <MintTokenForm
            onCancelAction={() => handleOpenChange(false)}
            onMintAction={onMintAction}
            onMintedAction={setMinted}
          />
        )
      ) : null}
    </Dialog>
  )
}

function MintTokenForm({
  onCancelAction,
  onMintAction,
  onMintedAction,
}: {
  onCancelAction: () => void
  onMintAction: (label: string | null) => Promise<MintOutcome>
  onMintedAction: (minted: MintedToken) => void
}) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<MintTokenValues>({
    // The label rule lives with the label (label-schema.ts), so both token
    // surfaces validate identically and the 255 here is the same 255 the
    // service enforces.
    resolver: zodResolver(mintTokenSchema),
    defaultValues: { label: '' },
  })

  const submit = handleSubmit(async (values) => {
    const trimmed = typeof values.label === 'string' ? values.label.trim() : ''
    const label = trimmed.length > 0 ? trimmed : null

    const outcome = await onMintAction(label)
    // A 401 is already navigating to the login screen. Saying anything here
    // would race that navigation with a toast about a token that does not exist.
    if ('redirecting' in outcome) return

    if (outcome.ok) {
      // Label and prefix only. The prefix is the public fragment of the
      // credential and is what lets the operator match this token later; the
      // credential itself goes to the step below and to nothing else.
      onMintedAction(outcome.minted)
      bigsuToast.success('MCP token created', {
        description: label
          ? `${outcome.minted.token.token_prefix} · ${label}`
          : outcome.minted.token.token_prefix,
      })
      return
    }

    // The backend's stable wording, surfaced verbatim against the field that
    // caused it (`invalid_token_label`, 400) and echoed once as a toast.
    setError('label', { type: 'server', message: outcome.message })
    bigsuToast.danger('Could not create the token', { description: outcome.message })
  })

  return (
    <DialogContent className="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Mint MCP token</DialogTitle>
        <DialogDescription>
          An MCP token authenticates one LibreChat identity to NOA. Paste it into the `noa` MCP
          server&rsquo;s customUserVars field after it is created.
        </DialogDescription>
      </DialogHeader>

      {/*
        The warning belongs here, BEFORE the mint — a notice that first appears
        beside the value has already stopped being a decision the operator can
        act on.
      */}
      <div
        role="note"
        className="flex items-start gap-3 rounded-md bg-status-warning-soft px-3 py-2 text-sm text-status-warning"
      >
        <BigsuIcon name="statusWarning" size="sm" aria-hidden />
        <p>
          The token is shown once, immediately after it is created, and cannot be recovered or
          displayed again. Have somewhere to paste it ready before you continue.
        </p>
      </div>

      <form onSubmit={submit} onKeyDown={submitOnEnter(submit)} className="flex flex-col gap-5">
        <FormField
          label="Label"
          errorText={errors.label?.message}
          helperText={`Optional. Names this token in the list so you can tell it apart later — up to ${MAX_LABEL_LENGTH} characters.`}
        >
          <Input
            {...register('label')}
            placeholder="LibreChat on my laptop"
            disabled={isSubmitting}
            autoComplete="off"
          />
        </FormField>
      </form>

      <DialogFooter>
        <Button variant="outline" onClick={onCancelAction} disabled={isSubmitting}>
          Cancel
        </Button>
        {/* Click handler, not a submit button — sandbox-inheriting tabs refuse form submission. */}
        <Button type="button" onClick={() => void submit()} loading={isSubmitting}>
          Mint token
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
