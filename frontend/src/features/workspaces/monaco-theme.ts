import type { Monaco } from '@monaco-editor/react'
import { readModeToken } from '@/lib/read-token'

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
  monaco.editor.defineTheme('nova-light', {
    base: 'vs',
    inherit: true,
    rules: [
      { token: 'keyword', foreground: 'd04738', fontStyle: 'bold' },
      { token: 'keyword.sql', foreground: 'd04738', fontStyle: 'bold' },
      { token: 'predefined.sql', foreground: 'd04738' },
      { token: 'operator.sql', foreground: 'd04738' },
      { token: 'string', foreground: '1a8974' },
      { token: 'string.sql', foreground: '1a8974' },
      { token: 'number', foreground: 'c9662e' },
      { token: 'comment', foreground: '8e99a4', fontStyle: 'italic' },
      { token: 'type', foreground: '6b46c1' },
      { token: 'identifier', foreground: '2c3e50' },
      { token: 'predefined', foreground: 'd04738' },
    ],
    colors: {
      'editor.background': readModeToken('--card', 'light', '#ffffff'),
      'editor.foreground': readModeToken('--foreground', 'light', '#2c3e50'),
      'editor.lineHighlightBackground': readModeToken('--muted', 'light', '#f8f9fa'),
      'editor.selectionBackground': readModeToken('--accent', 'light', '#fff0ed'),
      'editorCursor.foreground': readModeToken('--primary', 'light', '#d04738'),
    },
  })
  monaco.editor.defineTheme('nova-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: 'keyword', foreground: 'f36b5b', fontStyle: 'bold' },
      { token: 'keyword.sql', foreground: 'f36b5b', fontStyle: 'bold' },
      { token: 'predefined.sql', foreground: 'f36b5b' },
      { token: 'operator.sql', foreground: 'f36b5b' },
      { token: 'string', foreground: '2cc6b6' },
      { token: 'string.sql', foreground: '2cc6b6' },
      { token: 'number', foreground: 'f0ad3d' },
      { token: 'comment', foreground: '6b7a8d', fontStyle: 'italic' },
      { token: 'type', foreground: 'a78bfa' },
      { token: 'identifier', foreground: 'e2e8f0' },
      { token: 'predefined', foreground: 'f36b5b' },
    ],
    colors: {
      'editor.background': readModeToken('--card', 'dark', '#0f1117'),
      'editor.foreground': readModeToken('--foreground', 'dark', '#e2e8f0'),
      'editor.lineHighlightBackground': readModeToken('--muted', 'dark', '#1a1d2e'),
      'editor.selectionBackground': readModeToken('--accent', 'dark', '#202833'),
      'editorCursor.foreground': readModeToken('--primary', 'dark', '#d04538'),
    },
  })
  monaco.editor.setTheme(dark ? 'nova-dark' : 'nova-light')
}
