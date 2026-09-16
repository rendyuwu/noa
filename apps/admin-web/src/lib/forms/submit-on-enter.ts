import type { KeyboardEvent } from 'react'

/**
 * Enter in a text field saves the form, done in JS rather than by the browser.
 *
 * Every primary action in this app is a `type="button"` click handler, because the tab an operator
 * reaches admin from can inherit LibreChat's sandbox, where `allow-forms` is absent and a form
 * submission is refused before the `submit` event fires (`src/primary-action-not-submit.test.ts`
 * owns that rule). The cost, which went unmeasured until it was: with no submit button associated
 * with it, a form with more than one field that blocks implicit submission has nothing for the
 * browser's implicit submission to click, so Enter does nothing. MEASURED in Chromium 151 against
 * the rendered login page: Enter in the email field and Enter in the password field each produced
 * zero `submit` events and zero requests, while clicking the button posted once.
 *
 * So Enter is restored the same way the click is — by calling the handler. Nothing here submits a
 * form, so it works in the sandboxed tab too, where a hidden submit button would still be inert.
 *
 * The target test is the HTML implicit-submission rule, deliberately: this fires exactly where the
 * browser would have, and nowhere else. A `<textarea>` keeps Enter for newlines (the WHM private
 * key field is one), and every control that handles Enter itself — a Radix select or multi-select
 * trigger, a checkbox, a radio, a button — is a `<button>` rather than an `<input>` of a listed
 * type, so it is left alone.
 */

/** The spec's "fields that block implicit submission", which is where Enter would have submitted. */
const IMPLICIT_SUBMIT_INPUT_TYPES = new Set([
  'text',
  'search',
  'url',
  'tel',
  'email',
  'password',
  'date',
  'month',
  'week',
  'time',
  'datetime-local',
  'number',
])

/** An `onKeyDown` for a `<form>`, routing Enter to the same handler its `onSubmit` calls. */
export function submitOnEnter(submit: () => unknown) {
  return (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Enter') return
    // Enter while an IME is composing confirms a candidate; it is not a save.
    if (event.nativeEvent.isComposing) return
    // A modifier turns Enter into something else (newline, open in new tab); leave those alone.
    if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return

    const target = event.target
    if (!(target instanceof HTMLInputElement)) return
    // `.type` normalises an unknown or missing type to `text`, which is also what the browser does
    // when it decides whether the field blocks implicit submission.
    if (!IMPLICIT_SUBMIT_INPUT_TYPES.has(target.type)) return

    // Stops the native submission where one would have happened anyway (a single-field form still
    // has an implicit submission), so the handler runs once rather than twice.
    event.preventDefault()
    void submit()
  }
}
