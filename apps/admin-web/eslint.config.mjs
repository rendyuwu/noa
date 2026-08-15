import js from '@eslint/js'
import nextPlugin from '@next/eslint-plugin-next'
import { defineConfig, globalIgnores } from 'eslint/config'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

// Import firewall. Both entries hold a boundary that is otherwise only prose.
//
// `apps/web-embed` — the two web apps are independent packages with their own
// lockfiles, own CI and own deploy artifact. They share no source, no deps, no
// aliases and no symlinks (AGENTS.md, C12). The mirror of this rule lives in
// `apps/web-embed/eslint.config.mjs`.
//
// `noa-old` / `web-bigsu` — this panel was PORTED from the old repo, not linked
// to it (C13, V69, §T.48). A copy that keeps a path back to its source is not a
// copy: the old tree is a reference, it is not on the deploy artifact, and a
// build that resolved one of these would only do so on a developer's machine.
const forbiddenImports = {
  patterns: [
    {
      group: ['**/apps/web-embed', '**/apps/web-embed/**', '**/web-embed/**'],
      message:
        'apps/admin-web and apps/web-embed are independent packages — they share no source (C12, AGENTS.md).',
    },
    {
      group: ['**/noa-old/**', '**/web-bigsu', '**/web-bigsu/**'],
      message:
        'noa-old is a reference, not a dependency — §T.48 ports its files, it does not import them (C13, V69).',
    },
  ],
}

export default defineConfig(
  globalIgnores(['.next/', 'node_modules/', 'next-env.d.ts']),
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat['recommended-latest'],
  nextPlugin.configs.recommended,
  {
    rules: {
      'no-restricted-imports': ['error', forbiddenImports],
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
)
