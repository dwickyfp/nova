import './monaco-setup'
import type { Monaco } from '@monaco-editor/react'
import { readModeToken, readToken } from '@/lib/read-token'

/**
 * Syntax roles, read from the --code-* tokens in theme.css so the worksheet
 * editor, the notebook cell and highlighted markdown share one palette.
 */
const TOKEN_COLORS: Record<string, string> = {
  keyword: '--code-keyword',
  'keyword.sql': '--code-keyword',
  'predefined.sql': '--code-keyword',
  'operator.sql': '--code-keyword',
  string: '--code-string',
  'string.sql': '--code-string',
  number: '--code-number',
  comment: '--code-comment',
  type: '--code-type',
  identifier: '--foreground',
  predefined: '--code-keyword',
}

const FONT_STYLES: Record<string, string> = {
  keyword: 'bold',
  'keyword.sql': 'bold',
  comment: 'italic',
}

type Rule = { token: string; foreground: string; fontStyle?: string }

/** Monaco takes bare hex; `read` resolves a token for the mode being defined. */
function syntaxRules(read: (name: string) => string): Rule[] {
  return Object.entries(TOKEN_COLORS).map(([token, name]) => ({
    token,
    foreground: read(name).replace(/^#/, ''),
    ...(FONT_STYLES[token] ? { fontStyle: FONT_STYLES[token] } : {}),
  }))
}

/**
 * Registers the two Nova SQL themes on a Monaco namespace and selects one.
 *
 * Shared by the worksheet editor and the read-only version preview so a
 * snapshot looks exactly like the file it came from. Definition is idempotent:
 * Monaco replaces a theme of the same name, so mounting a second editor does
 * not append duplicates, and re-calling it after a theme switch re-reads the
 * CSS tokens instead of keeping the ones captured at first mount.
 *
 * Colors are read per mode via `readModeToken`, not off the live document: both
 * themes are defined in the same call, so reading the current class would bake
 * the active mode into both. `dark` only chooses which one is selected.
 */
export function applyNovaSqlTheme(monaco: Monaco, dark: boolean) {
  const lightForeground = readModeToken('--foreground', 'light', '#1e293b')
  const darkForeground = readModeToken('--foreground', 'dark', '#f7f8fa')
  monaco.editor.defineTheme('nova-light', {
    base: 'vs',
    inherit: true,
    rules: syntaxRules((name) => readModeToken(name, 'light', lightForeground)),
    colors: {
      'editor.background': readModeToken('--card', 'light', '#ffffff'),
      'editor.foreground': lightForeground,
      'editor.lineHighlightBackground': readModeToken('--muted', 'light', '#f8f9fa'),
      'editor.selectionBackground': readModeToken('--editor-selection', 'light', '#cfe0f1'),
      'editorCursor.foreground': readModeToken('--brand', 'light', '#2f6fb0'),
    },
  })
  monaco.editor.defineTheme('nova-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: syntaxRules((name) => readModeToken(name, 'dark', darkForeground)),
    colors: {
      'editor.background': readModeToken('--card', 'dark', '#0f1117'),
      'editor.foreground': darkForeground,
      'editor.lineHighlightBackground': readModeToken('--muted', 'dark', '#1a1d2e'),
      'editor.selectionBackground': readModeToken('--editor-selection', 'dark', '#24405f'),
      'editorCursor.foreground': readModeToken('--brand', 'dark', '#6aa5e0'),
    },
  })
  monaco.editor.setTheme(dark ? 'nova-dark' : 'nova-light')
}

/**
 * Notebook cell themes: the preview's syntax tokens on a transparent editor,
 * so the cell's own surface shows through in
 * either mode. Only the current mode is (re)defined, from the live page: read
 * per mode, a light theme built while the page is dark would get dark values.
 * Call again when the theme changes; pass the returned name to the editor.
 */
let cellThemeVersion = 0

export function applyNovaCellTheme(monaco: Monaco, dark: boolean): string {
  const mode = dark ? 'dark' : 'light'
  const clear = readToken('--editor-surface-clear', '#00000000')
  const foreground = readToken('--foreground', '#2c3e50')
  // A new name each time: Monaco does not repaint when the active theme is
  // redefined under the same name.
  cellThemeVersion += 1
  const name = `nova-cell-${mode}-${cellThemeVersion}`
  monaco.editor.defineTheme(name, {
    base: dark ? 'vs-dark' : 'vs',
    inherit: true,
    rules: syntaxRules((name) => readToken(name, foreground)),
    colors: {
      'editor.background': clear,
      'editorGutter.background': clear,
      'editor.lineHighlightBackground': clear,
      'editor.lineHighlightBorder': clear,
      'editorLineNumber.foreground': readToken('--muted-foreground', '#6b7685'),
      'editorLineNumber.activeForeground': readToken('--foreground', '#2c3e50'),
      'editor.foreground': readToken('--foreground', '#2c3e50'),
    },
  })
  monaco.editor.setTheme(name)
  return name
}
