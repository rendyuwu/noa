import js from '@eslint/js'
import nextPlugin from '@next/eslint-plugin-next'
import { defineConfig, globalIgnores } from 'eslint/config'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

// Import firewall. Both entries hold a boundary that is otherwise only prose.
//
// `apps/admin-web` — the two web apps are independent packages with their own
// lockfiles, own CI and own deploy artifact. They share no source, no deps, no
// aliases and no symlinks (AGENTS.md).
//
// `@gio/*` — the BIGSU design system belongs to `apps/admin-web` (that app's
// contract names it; this one's does not). The embed is a card in a small iframe and styles itself with
// hand-written CSS. Adopting BIGSU here is a decision that has to delete this
// rule on purpose, not a dependency that arrives by accident.
const forbiddenImports = {
  patterns: [
    {
      group: ['**/apps/admin-web', '**/apps/admin-web/**', '**/admin-web/**'],
      message:
        'apps/web-embed and apps/admin-web are independent packages — they share no source (AGENTS.md).',
    },
    {
      group: ['@gio/*'],
      message:
        'The embed app does not depend on BIGSU. Adding it is a deliberate design change, not an import.',
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
    // The harness scripts — Playwright's stub upstream, and the build-and-serve check that proves
    // the framing origin survives into the shipped server — are plain Node programs, not app code:
    // they run outside the bundler, so the browser-shaped default globals do not describe them.
    // Named by glob rather than app-wide — a `process` reference inside `src/` should still be an
    // error, which is the whole point of the one place `src/` is now allowed to make one.
    files: ['e2e/support/**/*.mjs', 'tests/**/*.mjs'],
    languageOptions: {
      globals: {
        process: 'readonly',
        URL: 'readonly',
        fetch: 'readonly',
        setTimeout: 'readonly',
      },
    },
  },
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
