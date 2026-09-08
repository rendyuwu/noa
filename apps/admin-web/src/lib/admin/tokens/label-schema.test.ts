import { describe, expect, it } from 'vitest'

import { MAX_LABEL_LENGTH, mintTokenSchema, tokenLabelSchema } from './label-schema'

describe('tokenLabelSchema', () => {
  it('accepts an absent label — an unnamed token is legitimate', () => {
    expect(tokenLabelSchema.safeParse(undefined).success).toBe(true)
  })

  it('trims before measuring, so padding is not a name and not a length', () => {
    const parsed = tokenLabelSchema.safeParse('  laptop  ')
    expect(parsed.success).toBe(true)
    expect(parsed.success && parsed.data).toBe('laptop')

    // 255 characters plus surrounding whitespace still fits, because the cap
    // applies to what is actually sent.
    expect(tokenLabelSchema.safeParse(`  ${'a'.repeat(MAX_LABEL_LENGTH)}  `).success).toBe(true)
  })

  it('mirrors the service cap at 255 and refuses 256', () => {
    expect(MAX_LABEL_LENGTH).toBe(255)
    expect(tokenLabelSchema.safeParse('a'.repeat(MAX_LABEL_LENGTH)).success).toBe(true)

    const tooLong = tokenLabelSchema.safeParse('a'.repeat(MAX_LABEL_LENGTH + 1))
    expect(tooLong.success).toBe(false)
    expect(tooLong.success === false && tooLong.error.issues[0]?.message).toBe(
      'Label must be 255 characters or fewer',
    )
  })
})

describe('mintTokenSchema', () => {
  it('is the field rule inside the form object both token surfaces resolve against', () => {
    expect(mintTokenSchema.safeParse({ label: 'laptop' }).success).toBe(true)
    expect(mintTokenSchema.safeParse({ label: '' }).success).toBe(true)
    expect(mintTokenSchema.safeParse({ label: 'a'.repeat(300) }).success).toBe(false)
  })
})
