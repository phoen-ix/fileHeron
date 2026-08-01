import js from '@eslint/js'
import a11y from 'eslint-plugin-vuejs-accessibility'
import vue from 'eslint-plugin-vue'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import prettierConfig from 'eslint-config-prettier'

export default [
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs['flat/recommended'],
  prettierConfig,
  // Accessibility, deliberately scoped. The full flat/recommended set produces
  // hundreds of findings and would stall any work behind it; these six are the
  // rules that catch the defect class the 2026-07-30 audit actually found -
  // interactive handlers on non-interactive elements with no keyboard path, and
  // malformed ARIA. This is a RECURRENCE guard: it is what stops the fixes in
  // this wave from silently regressing without writing a test per component.
  // Widening the rule set is its own piece of work, not a side effect of this one.
  {
    files: ['**/*.vue'],
    plugins: { 'vuejs-accessibility': a11y },
    rules: {
      'vuejs-accessibility/click-events-have-key-events': 'error',
      'vuejs-accessibility/no-static-element-interactions': 'error',
      'vuejs-accessibility/aria-role': 'error',
      'vuejs-accessibility/aria-props': 'error',
      'vuejs-accessibility/role-has-required-aria-props': 'error',
      // Stays off, but NOT for the reason the note above gives for
      // label-has-for. It fired 38 times (audit #2) - 11 of them <select>,
      // which has no placeholder to fall back on, so a screen-reader admin
      // heard "combo box, collapsed, All" with no idea what it filtered, and
      // one was the config-backup FILE input, indistinguishable from the
      // passphrase field beside it. All 38 now carry an accessible name, but
      // most are BOUND (`:aria-label="t(...)"`) and this rule only recognises a
      // static attribute, so it cannot be the gate. The gate is
      // backend/tests/test_frontend_a11y_tokens.py, which understands both.
      'vuejs-accessibility/form-control-has-label': 'off',
      // label-has-for fires 161 times: the codebase uses a standalone <label> beside
      // its input rather than for/id or nesting. Real, but it is a 161-site
      // refactor, not a gate - it lands with the frontend accessibility work.
      'vuejs-accessibility/label-has-for': 'off',
    },
  },
  {
    languageOptions: {
      // ESLint 10 no longer assumes an environment. Without this every
      // `window`, `document`, `fetch`, `localStorage` and `setTimeout` in the
      // SPA is an undeclared global and `no-undef` fires 122 times - which
      // reads as "the upgrade broke the code" and is really "the config never
      // said where this code runs".
      globals: { ...globals.browser },
      parserOptions: {
        parser: tseslint.parser,
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
    rules: {
      'vue/multi-word-component-names': 'off',
      'vue/html-self-closing': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
    ignores: ['dist/', 'node_modules/'],
  },
]
