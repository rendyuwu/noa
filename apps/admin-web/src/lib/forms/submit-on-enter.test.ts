import type { KeyboardEvent } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { submitOnEnter } from './submit-on-enter'

/**
 * What this lane can and cannot claim.
 *
 * It measures the decision this helper makes: which key, which modifier, which element. That is the
 * new logic, and every branch below is reachable from a real keystroke.
 *
 * It does NOT measure the thing that made the helper necessary — that a browser will not submit a
 * multi-field form which has no submit button. jsdom does not implement implicit submission at all,
 * so it cannot tell the fixed page from the broken one. That measurement was taken by hand in
 * Chromium 151 against the rendered login page (zero submits and zero requests from Enter in either
 * field, one request from the button) and is recorded in `docs/admin-web.md`. This package has no
 * browser runner to repeat it in — `apps/web-embed` owns the only Playwright lane, and the two
 * packages share nothing. Stated, rather than left to read as coverage.
 *
 * Watched fail, three mutations of the helper, and one of them says something worth keeping:
 * dropping the `IMPLICIT_SUBMIT_INPUT_TYPES` check reddens `leaves a checkbox alone`; dropping the
 * `preventDefault` reddens `stops the native submission, so a single-field form does not save
 * twice`; removing the `instanceof HTMLInputElement` narrowing reddens **nothing**. The two checks
 * overlap — a textarea and a button are refused twice over, once for not being an `<input>` and
 * once for having no listed type — so neither mutation alone can show them, and the narrowing is
 * there for TypeScript rather than for the decision. The checkbox is the only case the type set
 * decides by itself, which is why it is the one that separates.
 */

function keydown(
  target: EventTarget & { type?: string },
  overrides: Partial<KeyboardEvent<HTMLElement>> = {},
) {
  const preventDefault = vi.fn()
  const event = {
    key: 'Enter',
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    nativeEvent: { isComposing: false },
    target,
    preventDefault,
    ...overrides,
  } as unknown as KeyboardEvent<HTMLElement>
  return { event, preventDefault }
}

function input(type: string) {
  const element = document.createElement('input')
  element.type = type
  return element
}

describe('submitOnEnter', () => {
  it('saves on Enter in a field the browser would have submitted from', () => {
    for (const type of ['text', 'email', 'password', 'number', 'search', 'date']) {
      const submit = vi.fn()
      const { event } = keydown(input(type))
      submitOnEnter(submit)(event)
      expect(submit, `Enter in input[type=${type}]`).toHaveBeenCalledTimes(1)
    }
  })

  it('stops the native submission, so a single-field form does not save twice', () => {
    // A form with one text field and no submit button DOES implicitly submit (measured in Chromium
    // 151). Without preventDefault the handler would run once here and once from `onSubmit`.
    const { event, preventDefault } = keydown(input('text'))
    submitOnEnter(vi.fn())(event)
    expect(preventDefault).toHaveBeenCalledTimes(1)
  })

  it('leaves a textarea alone, so Enter stays a newline', () => {
    // The WHM dialog's private key field is a textarea. Saving on Enter there would make it
    // impossible to paste a key by hand.
    const submit = vi.fn()
    const { event, preventDefault } = keydown(document.createElement('textarea'))
    submitOnEnter(submit)(event)
    expect(submit).not.toHaveBeenCalled()
    expect(preventDefault).not.toHaveBeenCalled()
  })

  it('leaves a checkbox alone', () => {
    // A checkbox does not block implicit submission, so the browser would not have submitted here.
    const submit = vi.fn()
    submitOnEnter(submit)(keydown(input('checkbox')).event)
    expect(submit).not.toHaveBeenCalled()
  })

  it('leaves a Radix-style trigger alone', () => {
    // BIGSU's Select, MultiSelect, RadioGroup and Checkbox all render a <button>, and each handles
    // Enter itself. Stealing it would break opening a select from the keyboard.
    const submit = vi.fn()
    submitOnEnter(submit)(keydown(document.createElement('button')).event)
    expect(submit).not.toHaveBeenCalled()
  })

  it('ignores any key that is not Enter', () => {
    const submit = vi.fn()
    submitOnEnter(submit)(keydown(input('text'), { key: 'a' }).event)
    submitOnEnter(submit)(keydown(input('text'), { key: 'Tab' }).event)
    expect(submit).not.toHaveBeenCalled()
  })

  it('ignores a modified Enter', () => {
    const submit = vi.fn()
    for (const modifier of ['shiftKey', 'ctrlKey', 'metaKey', 'altKey'] as const) {
      submitOnEnter(submit)(keydown(input('text'), { [modifier]: true }).event)
    }
    expect(submit).not.toHaveBeenCalled()
  })

  it('ignores the Enter that confirms an IME candidate', () => {
    const submit = vi.fn()
    const { event } = keydown(input('text'), {
      nativeEvent: { isComposing: true } as KeyboardEvent<HTMLElement>['nativeEvent'],
    })
    submitOnEnter(submit)(event)
    expect(submit).not.toHaveBeenCalled()
  })
})
