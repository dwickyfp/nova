// Serve Monaco from Nova's own bundle. The editor package otherwise loads its
// code from a public CDN, which leaves the editor blank on a deployment
// without internet access and adds an outside origin to every SQL session.
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api.js";
import "monaco-editor/esm/vs/editor/editor.all.js";
import "monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution.js";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution.js";
import "monaco-editor/esm/vs/basic-languages/sql/sql.contribution.js";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker.js?worker";

// The basic SQL, Python and Markdown languages run on the generic editor worker.
self.MonacoEnvironment = { getWorker: () => new EditorWorker() };

loader.config({ monaco });
