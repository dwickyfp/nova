import baselineConfig from './eslint.design-system-baseline.json' with { type: 'json' }

// Legacy debt from the Phase A audit. The gate is real for every file not
// listed, so a new violation cannot land. Each entry leaves the list with the
// refactor group that owns the file. Never add to it.
const baseline = new Set(baselineConfig.grandfathered)

export const PALETTE_COLORS = [
  'red',
  'orange',
  'amber',
  'yellow',
  'lime',
  'green',
  'emerald',
  'teal',
  'cyan',
  'sky',
  'blue',
  'indigo',
  'violet',
  'purple',
  'fuchsia',
  'pink',
  'rose',
  'slate',
  'gray',
  'zinc',
  'neutral',
  'stone',
]

export const PALETTE_UTILITIES = [
  'bg',
  'text',
  'border',
  'from',
  'via',
  'to',
  'ring',
  'fill',
  'stroke',
  'divide',
  'outline',
  'shadow',
  'caret',
  'decoration',
  'placeholder',
  'accent',
]

const paletteWord = PALETTE_COLORS.join('|')
const utilityWord = PALETTE_UTILITIES.join('|')

// Raw palette utility inside any string, including template literals and cn()
// argument lists, with optional variant prefixes and opacity.
export const PALETTE_PATTERN = String.raw`(?:\s|^|['"\`])(?:[a-z-]+:)*(?:${utilityWord})-(?:${paletteWord})-\d{2,3}(?:\/\d{1,3})?`

export const HEX_PATTERN = String.raw`#[0-9a-fA-F]{3,8}\b`

export const STORAGE_VENDOR_PATTERN = String.raw`(?:s3:\/\/|s3a:\/\/|gs:\/\/|gcs:\/\/|minio|\bazure\b|blob\.core\.windows\.net)`

const PALETTE_MESSAGE =
  'Raw Tailwind palette class in feature code. Use a semantic token instead (bg-success, text-warning-strong, border-destructive). See DESIGN.md section 3 A1.'

const HEX_MESSAGE =
  'Hex colour literal. Define the colour as a token in src/styles/theme.css and use the token. See DESIGN.md section 3 A2.'

const STORAGE_MESSAGE =
  'Storage vendor reference in the frontend. Use a stage name (@stage.file.csv) or a configured connection name. See DESIGN.md section 3 A3 and AGENTS.md rule #1.'

const normalize = (filename) =>
  filename.replace(/\\/g, '/').replace(/^.*?\/src\//, 'src/')

export const TOKEN_READER_NAME = /^read[A-Za-z]*Token$/

/**
 * A hex literal is a legitimate fallback when it is an argument to a token
 * reader whose first argument names a custom property, e.g.
 * readToken('--chart-1', '#f05a47'). Recharts needs concrete colour strings
 * and getComputedStyle does not exist on the server, so the fallback cannot be
 * removed. Restricting the exemption to that syntactic shape keeps it narrow:
 * the hex still cannot hide inside a className.
 */
export function isTokenFallbackArgument(node) {
  const call = node.parent
  if (!call || call.type !== 'CallExpression') return false

  if (call.callee.type !== 'Identifier') return false
  if (!TOKEN_READER_NAME.test(call.callee.name)) return false

  const firstArgument = call.arguments[0]
  if (!firstArgument || firstArgument.type !== 'Literal') return false
  if (typeof firstArgument.value !== 'string') return false

  return firstArgument.value.startsWith('--')
}

// Selectors cannot see the current filename, so the baseline lives in a rule
// wrapper instead of config matching: a grandfathered file is reported at
// "off" strength, everything else fails the build.
const gate = (checks, { allowTokenFallback = false } = {}) => ({
  meta: {
    type: 'problem',
    docs: {
      description:
        'Nova design system gate: semantic tokens only, no hex literals, no storage vendor names.',
    },
    schema: [],
  },
  create(context) {
    const grandfathered = baseline.has(normalize(context.filename))

    if (grandfathered) return {}

    const test = (node, value) => {
      if (typeof value !== 'string') return
      for (const [pattern, message] of checks) {
        const isHexCheck = pattern === HEX_PATTERN
        if (isHexCheck && allowTokenFallback && isTokenFallbackArgument(node)) {
          continue
        }
        if (new RegExp(pattern).test(value)) {
          context.report({ node, message })
        }
      }
    }

    return {
      Literal(node) {
        test(node, node.value)
      },
      TemplateElement(node) {
        test(node, node.value?.raw)
      },
    }
  },
})

const FEATURE_CHECKS = [
  [PALETTE_PATTERN, PALETTE_MESSAGE],
  [HEX_PATTERN, HEX_MESSAGE],
  [STORAGE_VENDOR_PATTERN, STORAGE_MESSAGE],
]

const REPOSITORY_CHECKS = [
  [HEX_PATTERN, HEX_MESSAGE],
  [STORAGE_VENDOR_PATTERN, STORAGE_MESSAGE],
]

export const novaPlugin = {
  rules: {
    'semantic-tokens': gate(REPOSITORY_CHECKS, { allowTokenFallback: true }),
    'semantic-tokens-features': gate(FEATURE_CHECKS, {
      allowTokenFallback: true,
    }),
  },
}

// Tests are excluded because a gate test must contain the very patterns it
// asserts are rejected. Excluding them does not weaken the gate: no test file
// renders the app, so a hex there cannot reach the UI.
const TEST_FILE = '**/*.test.{ts,tsx}'

// Order matters: the feature scope comes last so it replaces the repo-wide
// rule for files it matches.
export default [
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: [TEST_FILE],
    plugins: { nova: novaPlugin },
    rules: {
      'nova/semantic-tokens': 'error',
    },
  },
  {
    files: ['src/features/**/*.{ts,tsx}'],
    ignores: [TEST_FILE],
    rules: {
      'nova/semantic-tokens': 'off',
      'nova/semantic-tokens-features': 'error',
    },
  },
]
