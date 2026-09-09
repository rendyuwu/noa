'use client'

import { useState } from 'react'

import { Notice } from './notice'
import styles from './notice.module.css'

/**
 * The way out of a 401, and the only one this app is allowed to offer (§T.43, §T.56 — V38, V42).
 *
 * **A pointer, never a form.** Every surface here authenticates with the `noa_session` cookie (V22,
 * V40) and the MCP bearer token never reaches a browser (C5), so a 401 in here means the operator
 * has a working LibreChat token and no NOA browser session. V42 puts the remedy outside this app: no
 * login page, no LDAP form, no credential handling, and — §T.43 says it explicitly — **no LDAP
 * redirect inside the iframe**. Nothing in this component navigates. The frame stays on the URL it
 * was given, and signing in happens in a document of its own.
 *
 * **Two doors, because one of them can fail silently.** LibreChat frames these documents at two
 * render sites and only one of them grants `allow-popups` (R13, R29: `ToolCallInfo` =
 * `allow-scripts allow-same-origin`, `MCPUIResource` = that plus `allow-popups`). Where it is
 * absent, a `target="_blank"` click is blocked with no error the operator can see — V80's failure
 * shape, one mechanism over. So the address is *also* printed as text: the link is the door where
 * popups are allowed, and the printed address is the door where they are not (V94).
 *
 * No clipboard button beside it, deliberately: `navigator.clipboard` needs a permission a sandboxed
 * frame may not have, and a copy button that silently copies nothing is the failure this block
 * exists to route around.
 *
 * **Retry rather than reload.** The retry is the caller's own reader — the approval card hands its
 * poll (`lib/approvals/poll.ts`), the table surface hands its loader — so a session picked up in the
 * other tab turns this notice into the subject without the frame navigating anywhere.
 *
 * Shared by both surfaces since §T.56 (V66). The retry's type is structural for that reason: a
 * read's outcome is a discriminated union per surface, and all this needs is which kind came back.
 */

/** What a retry answered, in the words this notice says it in. Keyed by a load's `kind`. */
const RETRY_NOTES: Record<string, string> = {
  unauthenticated: 'NOA still does not recognise this session. Sign in first, then try again.',
  unavailable: 'NOA could not be reached just now. Try again in a moment.',
}

export function SignInNotice({
  signInUrl,
  onRetry,
  frameOrigin,
}: {
  /** `null` when no usable address is configured (`lib/sign-in.ts`) — then no link is rendered. */
  signInUrl: string | null
  /** Re-reads the subject. Resolves to whatever the read answered, so this notice can report it. */
  onRetry: () => Promise<{ kind: string }>
  /**
   * Passed straight through to `Notice`, which measures this state and asks the host for a frame it
   * fits in. It matters most here: the printed address below is the door the sandbox cannot withhold
   * (V94), and a frame too short to show it leaves that door off screen.
   */
  frameOrigin: string | null
}) {
  const [retrying, setRetrying] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  async function retry() {
    if (retrying) return

    setRetrying(true)
    setNote(null)
    try {
      const load = await onRetry()
      // A successful read replaces this notice entirely, so the only answers worth a word are the
      // ones that leave the operator looking at it (`not-found` included: the session came back and
      // the thing behind the URL is not theirs, or no longer exists).
      setNote(RETRY_NOTES[load.kind] ?? null)
    } finally {
      setRetrying(false)
    }
  }

  return (
    <Notice
      title="Cannot authenticate here"
      body="NOA does not recognise this session. Sign in to NOA in a new tab, then try again — this page cannot sign you in."
      frameOrigin={frameOrigin}
    >
      <div className={styles.actions}>
        {signInUrl ? (
          // `rel="noopener noreferrer"`: the tab this opens is a NOA document with the operator's
          // session in it, and it has no business holding a handle back to a framed page.
          <a
            className={`${styles.button} ${styles.noticeLink}`}
            href={signInUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            Sign in to NOA
          </a>
        ) : null}
        <button
          type="button"
          className={styles.button}
          onClick={() => void retry()}
          disabled={retrying}
        >
          {retrying ? 'Checking…' : 'Try again'}
        </button>
      </div>

      {signInUrl ? (
        <p className={styles.noticeBody}>
          If that link does not open, copy this address into a new tab:{' '}
          <span className={styles.noticeAddress}>{signInUrl}</span>
        </p>
      ) : null}

      {note ? (
        <p className={styles.noticeBody} role="status">
          {note}
        </p>
      ) : null}
    </Notice>
  )
}
