'use client'

import { useRef } from 'react'
import {
  Button,
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

import type { MintedToken } from '@/lib/admin/tokens/types'

export type MintedTokenPanelProps = {
  minted: MintedToken
  onDoneAction: () => void
}

// The one and only render site of a token plaintext in this application (V103).
// Everything about this component is shaped by that: it takes the value as a
// prop, holds no copy of it, derives nothing from it, and disappears with its
// parent the moment the dialog closes.
//
// Four deliberate choices in the field below, each closing a leak:
//
//  - `readOnly`, not `disabled` — the value must stay selectable for the
//    keyboard-copy fallback, and a disabled control is not.
//  - no `name` — a named control inside a form is submitted. There is no form
//    here (see the next point), and the missing `name` means there is nothing to
//    submit even if one were ever wrapped around it.
//  - outside any `<form>` — the house dialog wraps its body in
//    `<form onSubmit>` (server-form-dialog.tsx:109). Enter inside such a form
//    either re-fires submit or, with no submit handler, performs a native GET
//    that puts the token in the query string, the address bar and the history.
//    This panel is a sibling of that pattern, never a child of it.
//  - `autoComplete="off"` — a credential is not a value any password manager or
//    form-fill heuristic should learn.
//
// Losing the value to an Escape or an overlay click is not fought: overriding
// Radix dismissal is an accessibility deviation, and the recovery is cheap and
// stated below — mint another and revoke this one. That is the guarantee the
// backend actually makes (mcp_tokens.py:99-106).
export function MintedTokenPanel({ minted, onDoneAction }: MintedTokenPanelProps) {
  const fieldRef = useRef<HTMLInputElement>(null)

  // Select the value so the operator can copy it with the keyboard. This is the
  // path that runs whenever `navigator.clipboard` is missing — an insecure
  // context, an older browser, and jsdom, where it is absent by default.
  const selectField = () => {
    fieldRef.current?.focus()
    fieldRef.current?.select()
  }

  // Toast copy names the prefix, never the value: the prefix is the public
  // fragment of the credential (types.ts) and is exactly what the operator needs
  // to match this row later.
  const copy = () => {
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
    if (!clipboard) {
      selectField()
      bigsuToast.warning('Copy isn’t available here', {
        description: 'The token is selected — copy it with your keyboard.',
      })
      return
    }
    void clipboard.writeText(minted.plaintext).then(
      () => bigsuToast.success('Token copied', { description: minted.token.token_prefix }),
      () => {
        selectField()
        bigsuToast.danger('Could not copy the token', {
          description: 'The token is selected — copy it with your keyboard.',
        })
      },
    )
  }

  return (
    <DialogContent className="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>Token created</DialogTitle>
        <DialogDescription>
          Copy the token now and paste it into LibreChat. NOA stores only a hash of it, so this is
          the only time it can be shown.
        </DialogDescription>
      </DialogHeader>

      <div className="flex flex-col gap-4">
        <div
          role="note"
          className="flex items-start gap-3 rounded-md bg-status-danger-soft px-3 py-2 text-sm text-status-danger"
        >
          <BigsuIcon name="statusDanger" size="sm" aria-hidden />
          <p>
            Shown once. Closing this dialog discards the value permanently — it cannot be recovered
            or displayed again. If you lose it, mint a new token and revoke this one.
          </p>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <FormField
            className="min-w-0 flex-1"
            label="Token"
            helperText="Paste this into the LibreChat MCP server's customUserVars field."
          >
            <Input
              ref={fieldRef}
              readOnly
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
              value={minted.plaintext}
              onFocus={(event) => event.currentTarget.select()}
            />
          </FormField>
          <Button variant="secondary" onClick={copy}>
            <BigsuIcon name="export" size="sm" aria-hidden />
            Copy token
          </Button>
        </div>

        <dl className="flex items-center justify-between gap-4 text-sm">
          <dt className="text-text-secondary">Prefix</dt>
          <dd className="font-mono text-text-primary" data-testid="minted-token-prefix">
            {minted.token.token_prefix}
          </dd>
        </dl>
      </div>

      <DialogFooter>
        <Button onClick={onDoneAction}>Done</Button>
      </DialogFooter>
    </DialogContent>
  )
}
