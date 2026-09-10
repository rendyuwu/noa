import { z } from 'zod'

// The Zod schema for the mint-token form.
//
// 255 is the label cap in `core/auth/mcp_token_service.py:76`, and THAT is the
// authoritative one: the service normalises a blank label to `None` and rejects
// an over-long one with 400 `invalid_token_label` in `_validate_label`
// (:276-290), so a bootstrap script or a future CLI is held to the same rule.
// This check saves the operator a round trip; it does not define the limit, and
// the backend's wording is still what surfaces when the two ever disagree.
export const MAX_LABEL_LENGTH = 255

// Optional because `mcp_tokens.label` is nullable — an unnamed token is a
// legitimate thing to mint, and requiring a name here would be a rule the schema
// does not have. Trimmed before the length check for the same reason the
// transport trims: trailing whitespace is not a name.
export const tokenLabelSchema: z.ZodType<string | undefined> = z
  .string()
  .trim()
  .max(MAX_LABEL_LENGTH, `Label must be ${MAX_LABEL_LENGTH} characters or fewer`)
  .optional()

// The form object react-hook-form resolves against. Kept here beside the field
// rule rather than inlined at the dialog, so the two token surfaces (self and
// admin) validate identically.
export const mintTokenSchema = z.object({ label: tokenLabelSchema })

export type MintTokenValues = z.infer<typeof mintTokenSchema>
