import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * The decision buttons' colours, read out of the stylesheets that ship them.
 *
 * This reads CSS as text rather than rendering it, and that is the point: it binds the hex values
 * actually in the files, so editing one back under the bar reddens here. A hard-coded expected
 * ratio would not — it would go on passing against whatever colour the file had been changed to.
 *
 * Two surfaces per colour, because there are two. At rest the button's background is `--noa-bg`;
 * under the pointer it is `currentColor` mixed into `--noa-bg`, which lifts the surface toward the
 * label and takes the contrast DOWN. A colour chosen against the resting number alone can sit
 * under the threshold for as long as the pointer is on it, so both are asserted.
 *
 * 4.5:1 is the bar and not 3:1: the buttons are `font-weight: 600` at the body's 0.9375rem (15px),
 * and large text starts at 18.66px bold.
 *
 * **What this does NOT bind**: that `.approve` and `.deny` still reach a button at all. Nothing
 * here reads `decision-controls.tsx`, so dropping the class from the Approve button leaves every
 * assertion below green. The colours are checked, the wiring is not — stated rather than left to
 * read as coverage.
 */

const MIN_RATIO = 4.5

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')

const cardCss = read('./card.module.css')
const noticeCss = read('../../../components/notice.module.css')
const globalsCss = read('../../globals.css')

/**
 * Split a stylesheet into its light half and its dark half.
 *
 * Both files state the light value first and override it inside the dark media query, so "before"
 * and "after" the query is the whole story. If that query is ever renamed or removed the split
 * returns one part and every dark lookup below fails to match — which is the failure we want, not
 * a silent fall back to the light value.
 */
function bySchemeHalf(css: string): { light: string; dark: string } {
  const [light, dark, ...extra] = css.split(/@media\s*\(\s*prefers-color-scheme:\s*dark\s*\)/)
  if (light === undefined || dark === undefined || extra.length > 0) {
    throw new Error('expected exactly one prefers-color-scheme: dark block in the stylesheet')
  }
  return { light, dark }
}

/**
 * Pull one capture out of `css`, anchored on the selector or property the regex names.
 *
 * Anchored, always: a regex that scans the whole file for `#[0-9a-f]{6}` would keep matching after
 * `.approve` had been renamed to something the buttons no longer use. Throwing on a miss turns a
 * rename into a red test instead of a value silently read from the wrong rule.
 */
function capture(css: string, pattern: RegExp, what: string): string {
  const found = css.match(pattern)
  if (!found?.[1]) throw new Error(`no ${what} in the stylesheet — renamed or removed?`)
  return found[1]
}

/** `.selector { … color: #rrggbb … }`, the colour of the rule that selector opens. */
const colorOf = (selector: string) =>
  new RegExp(`\\${selector}\\s*\\{[^}]*?\\bcolor:\\s*(#[0-9a-fA-F]{6})`)

/** `--name: #rrggbb`, the token as declared in whichever half it is read from. */
const tokenOf = (name: string) => new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`)

const card = bySchemeHalf(cardCss)
const globals = bySchemeHalf(globalsCss)

const backgrounds = {
  light: capture(globals.light, tokenOf('--noa-bg'), 'light --noa-bg'),
  dark: capture(globals.dark, tokenOf('--noa-bg'), 'dark --noa-bg'),
}

const labels = {
  light: {
    approve: capture(card.light, colorOf('.approve'), 'light .approve colour'),
    deny: capture(card.light, colorOf('.deny'), 'light .deny colour'),
  },
  dark: {
    approve: capture(card.dark, colorOf('.approve'), 'dark .approve colour'),
    deny: capture(card.dark, colorOf('.deny'), 'dark .deny colour'),
  },
}

/**
 * The hover tint's strength, read from the rule that applies it rather than repeated here.
 *
 * Raising the percentage moves every hovered surface further toward its label and every hovered
 * ratio down, so this number belongs in the calculation and not in a constant beside it.
 */
const mixPercent = Number(
  capture(
    noticeCss,
    /\.button:hover[^{]*\{[^}]*?color-mix\(\s*in\s+srgb\s*,\s*currentColor\s+(\d+(?:\.\d+)?)%/,
    'hover color-mix percentage',
  ),
)

const channels = (hex: string): readonly [number, number, number] => [
  parseInt(hex.slice(1, 3), 16) / 255,
  parseInt(hex.slice(3, 5), 16) / 255,
  parseInt(hex.slice(5, 7), 16) / 255,
]

const linear = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)

/** WCAG 2 relative luminance. */
function luminance(hex: string): number {
  const [r, g, b] = channels(hex)
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
}

function contrast(a: string, b: string): number {
  const one = luminance(a)
  const other = luminance(b)
  return (Math.max(one, other) + 0.05) / (Math.min(one, other) + 0.05)
}

/**
 * `color-mix(in srgb, fg p%, bg)`.
 *
 * Channel-wise on the gamma-encoded values, because `in srgb` names the gamma-encoded space — the
 * browser does not linearise first, and mixing as if it did would report a surface the operator
 * never sees.
 */
function mix(front: string, back: string, percent: number): string {
  const p = percent / 100
  const [fr, fg, fb] = channels(front)
  const [br, bg, bb] = channels(back)
  const byte = (f: number, b: number) =>
    Math.round((p * f + (1 - p) * b) * 255)
      .toString(16)
      .padStart(2, '0')
  return `#${byte(fr, br)}${byte(fg, bg)}${byte(fb, bb)}`
}

const cases = (['light', 'dark'] as const).flatMap((scheme) =>
  (['approve', 'deny'] as const).map((which) => {
    const label = labels[scheme][which]
    const bg = backgrounds[scheme]
    return {
      name: `${which} ${scheme} (${label} on ${bg})`,
      rest: contrast(label, bg),
      hovered: contrast(label, mix(label, bg, mixPercent)),
    }
  }),
)

describe('decision button contrast', () => {
  it('reads a hover tint to measure against', () => {
    // A miss here would leave every hovered ratio equal to its resting one and the suite green for
    // the wrong reason.
    expect(mixPercent).toBeGreaterThan(0)
  })

  for (const { name, rest, hovered } of cases) {
    it(`${name} clears 4.5:1 at rest and hovered`, () => {
      expect(rest).toBeGreaterThanOrEqual(MIN_RATIO)
      expect(hovered).toBeGreaterThanOrEqual(MIN_RATIO)
    })
  }
})

describe('decision button hover and focus', () => {
  // Anchored on the selector text: these assert the states exist and take their colour from the
  // button, which is what makes one declaration cover Approve, Deny, Copy summary and the link-out.
  it.each(['.button:hover', '.button:focus-visible'])('%s tints from currentColor', (selector) => {
    const rule = noticeCss.match(new RegExp(`\\${selector}[^{]*\\{([^}]*)\\}`))
    expect(rule?.[1]).toContain('currentColor')
  })
})
