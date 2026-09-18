#!/usr/bin/env bash
#
# Regenerate the Nova ANTLR4 Python parser from the vendored StarRocks grammar.
#
# This is the canonical generator. CI runs it and then `git diff --exit-code` on
# `backend/app/sql_dialect/grammar/` to prove the committed artifacts match the
# grammar sources; developers run the same script after editing a `.g4`.
#
# Java is a BUILD-TIME dependency only. The runtime and request path stay pure
# Python (`antlr4-python3-runtime`), per the NOVA-17 build-tooling carve-out in
# `AGENTS.md`.
#
# Usage:
#   backend/scripts/generate_grammar.sh
#
# Environment:
#   ANTLR_JAR  path to antlr-4.13.2-complete.jar (default: ./.antlr/antlr-4.13.2-complete.jar
#              relative to the repo root, downloaded if absent)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GRAMMAR_DIR="$REPO_ROOT/backend/app/sql_dialect/grammar"
ANTLR_VERSION="4.13.2"

ANTLR_JAR="${ANTLR_JAR:-$REPO_ROOT/.antlr/antlr-$ANTLR_VERSION-complete.jar}"

if [[ ! -f "$ANTLR_JAR" ]]; then
    mkdir -p "$(dirname "$ANTLR_JAR")"
    echo "Downloading antlr-$ANTLR_VERSION-complete.jar -> $ANTLR_JAR"
    curl -fsSL -o "$ANTLR_JAR" \
        "https://www.antlr.org/download/antlr-$ANTLR_VERSION-complete.jar"
fi

if ! command -v java >/dev/null 2>&1; then
    echo "error: java not found; ANTLR generation needs a JVM (build time only)" >&2
    exit 1
fi

cd "$GRAMMAR_DIR"
# Generated artifacts are overwritten unconditionally; removing them first makes
# a stale artifact from a renamed rule impossible to miss in the diff.
rm -f StarRocksLexer.py StarRocksParser.py StarRocksVisitor.py \
      StarRocks.interp StarRocksLexer.interp \
      StarRocks.tokens StarRocksLexer.tokens

java -jar "$ANTLR_JAR" \
    -Dlanguage=Python3 \
    -visitor \
    -no-listener \
    -o . \
    StarRocks.g4

# ANTLR also emits `.interp` ATN-serialization files. They are consumed by the
# ANTLR tooling/visualiser only -- the parser embeds its own serialized ATN in
# `serializedATN()` inside the `.py` and never opens them. They are ~530 KB of
# pure churn on every regeneration, so they are not committed; a test in
# `tests/unit/test_sql_dialect_grammar.py` proves the parser works without them.
rm -f StarRocks.interp StarRocksLexer.interp

echo "Regenerated Python artifacts in $GRAMMAR_DIR"
