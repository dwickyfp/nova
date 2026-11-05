// @openuidev/react-lang mounts its "OpenUI Inspect" button over every page in
// development. It skips the mount when this global flag is already set, so this
// module must load before any module that imports the package.
;(globalThis as Record<symbol, unknown>)[
  Symbol.for('openui.devtools.autoMount')
] = true

export {}
