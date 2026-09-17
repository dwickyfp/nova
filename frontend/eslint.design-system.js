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
 *
 * A value built by concatenation (e.g. readToken('--x', '#' + 'd04738')) is
 * also exempt when it sits directly in that argument slot: the call site is the
 * only thing that makes the fallback legitimate, and the first argument is
 * still a literal custom property name.
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

/**
 * Conservative static string evaluation. It answers only what can be known
 * from syntax alone, so it cannot false-positive on a value that is genuinely
 * dynamic:
 *
 *  - `null` means "not a statically known string". An Identifier, a call, a
 *    member access, or a concat with an unknown operand all evaluate to null,
 *    and null is never matched against a pattern.
 *  - unknown fragments are not treated as an empty string. A partially
 *    dynamic value is not reconstructed at all, so `p-2 ${c}` cannot be read
 *    as the safe literal `p-2` while the palette literal bound to `c` slips
 *    past — that literal is still caught where it is written.
 */
export const UNKNOWN = null

export const staticString = (node) => {
  if (!node) return UNKNOWN

  switch (node.type) {
    case 'Literal':
      return typeof node.value === 'string' ? node.value : UNKNOWN

    case 'TemplateLiteral': {
      let out = ''
      for (let i = 0; i < node.quasis.length; i += 1) {
        out += node.quasis[i].value?.cooked ?? node.quasis[i].value?.raw ?? ''
        const expression = node.expressions[i]
        if (!expression) continue
        const value = staticString(expression)
        if (value === UNKNOWN) return UNKNOWN
        out += value
      }
      return out
    }

    case 'BinaryExpression': {
      if (node.operator !== '+') return UNKNOWN
      const left = staticString(node.left)
      const right = staticString(node.right)
      if (left === UNKNOWN || right === UNKNOWN) return UNKNOWN
      return left + right
    }

    case 'CallExpression': {
      const separator = joinedSeparator(node)
      if (separator === UNKNOWN) return UNKNOWN
      const parts = []
      for (const element of node.callee.object.elements) {
        if (!element || element.type === 'SpreadElement') return UNKNOWN
        const value = staticString(element)
        if (value === UNKNOWN) return UNKNOWN
        parts.push(value)
      }
      return parts.join(separator)
    }

    default:
      return UNKNOWN
  }
}

/**
 * Recognises `['a', 'b'].join('-')` and returns the separator, or UNKNOWN for
 * anything else. The separator itself must be a static string, otherwise the
 * join result is not knowable.
 */
export const joinedSeparator = (node) => {
  if (node.callee?.type !== 'MemberExpression') return UNKNOWN
  if (node.callee.computed) return UNKNOWN
  if (node.callee.property?.name !== 'join') return UNKNOWN
  if (node.callee.object?.type !== 'ArrayExpression') return UNKNOWN

  const separator = node.arguments?.[0]
  if (separator === undefined) return ''
  return staticString(separator)
}

/**
 * The concrete strings a value expression denotes, for the gate to test.
 *
 * A value assembled purely from literals denotes exactly one string; that is
 * the case the Literal visitor cannot see, because the palette class or hex
 * never exists as a single Literal node.
 *
 * A value that mixes known text with an unknown fragment denotes no single
 * string, so this returns nothing for it. It deliberately does not return the
 * known fragments either: a fragment such as `p-2` is not a value, and
 * matching fragments in isolation is how a gate starts reporting on strings
 * the code never builds. The unknown operand is covered where it is written.
 */
export const assembledValues = (node) => {
  const value = staticString(node)
  return value === UNKNOWN ? [] : [value]
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

    // A value assembled from literals is invisible to the Literal visitor: the
    // palette class never exists as a single Literal node. Reconstructing the
    // string from syntax closes that, and only that, hole.
    const testAssembled = (node) => {
      for (const value of assembledValues(node)) {
        test(node, value)
      }
    }

    return {
      Literal(node) {
        test(node, node.value)
      },
      TemplateElement(node) {
        test(node, node.value?.raw)
      },
      // Only the outermost expression is evaluated: an inner concat is part of
      // the parent's reconstructed value, and reporting it separately would
      // duplicate the diagnostic for one string.
      'BinaryExpression:exit'(node) {
        if (node.parent?.type === 'BinaryExpression') return
        testAssembled(node)
      },
      'TemplateLiteral:exit'(node) {
        testAssembled(node)
      },
      'CallExpression[callee.property.name="join"]'(node) {
        if (node.parent?.type === 'BinaryExpression') return
        testAssembled(node)
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
