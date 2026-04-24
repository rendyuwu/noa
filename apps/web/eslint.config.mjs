import js from "@eslint/js";
import nextPlugin from "@next/eslint-plugin-next";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  // Global ignores
  { ignores: [".next/", "node_modules/"] },

  // Base JS recommended rules
  js.configs.recommended,

  // TypeScript recommended (type-aware disabled — keeps lint fast)
  ...tseslint.configs.recommended,

  // React Hooks (flat config variant)
  reactHooks.configs.flat["recommended-latest"],

  // Next.js recommended
  nextPlugin.configs.recommended,

  // Project overrides
  {
    rules: {
      // Match existing code style: unused vars prefixed with _ are OK
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],

      // Existing code uses `any` in a few places; warn, don't error
      "@typescript-eslint/no-explicit-any": "warn",

      // Allow empty interfaces (used for module augmentation in assistant.config.ts)
      "@typescript-eslint/no-empty-object-type": "off",

      // Allow namespace declarations (used for assistant-ui module augmentation)
      "@typescript-eslint/no-namespace": "off",

      // preserve-caught-error: warn only (re-throwing with cause is intentional)
      "preserve-caught-error": "off",

      // Allow empty catch blocks (intentional try/catch for localStorage, etc.)
      "no-empty": ["error", { allowEmptyCatch: true }],

      // React Compiler rules — too strict for initial adoption; warn only.
      // setState in effects is a common pattern for syncing with external state
      // (localStorage, URL params, etc.)
      "react-hooks/set-state-in-effect": "warn",

      // Components defined inside render should be extracted, but warn for now
      "react-hooks/static-components": "warn",

      // Ref assignment during render — common pattern for "latest ref" values
      "react-hooks/refs": "warn",

      // React Compiler memoization preservation — warn only
      "react-hooks/preserve-manual-memoization": "warn",
    },
  },

  // Test file overrides
  {
    files: ["**/*.test.ts", "**/*.test.tsx"],
    rules: {
      // Tests use require() for dynamic mocking
      "@typescript-eslint/no-require-imports": "off",

      // Tests assign to `module` for mocking
      "@next/next/no-assign-module-variable": "off",

      // Test setup assignments (e.g., React = ...) are intentional
      "no-useless-assignment": "off",
    },
  },
);
