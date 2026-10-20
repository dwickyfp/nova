import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import designSystem from './eslint.design-system.js'

export default tseslint.config(
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      '*.config.js',
      '*.config.cjs',
      '*.config.mjs',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
      },
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
      },
    },
  },
  {
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // Needed so the existing react-refresh/only-export-components disables
      // on the provider files resolve instead of erroring as ghost rules.
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // Catches a hook called conditionally (e.g. a chained `a ?? useHook()`
      // short-circuit). That class of bug crashed WorkspacesPage with
      // "change in the order of Hooks"; the rule is enabled so it cannot recur.
      'react-hooks/rules-of-hooks': 'error',
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
        },
      ],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-empty-object-type': 'off',
    },
  },
  ...designSystem
)
