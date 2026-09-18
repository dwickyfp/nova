"""@stage SQL dialect — parse, translate, inject credentials, detect formats.

Nova's custom SQL dialect: @stage_name.path.file.csv
Translates to StarRocks FILES() function with auto-detected format + injected credentials.

Examples:
    SELECT * FROM @stage1.data.csv
    → SELECT * FROM FILES('path'='s3://bucket/prefix/data.csv', 'format'='csv', creds...)

    SELECT * FROM @silver.stage1.folder.file.parquet
    → SELECT * FROM FILES('path'='s3://.../folder/file.parquet', 'format'='parquet', creds...)

The stage registry is built from the **ANTLR4 parse tree** (NOVA-126 / 109-B),
not from a regex over the raw text. ``StarRocks.g4`` carries a Nova
``stageReference`` rule in ``relationPrimary`` (added by NOVA-125 / 109-A), so a
``@stage`` is a stage exactly where the grammar says a table may stand. That is
what closes the NOVA-17 parse defects this slice owns:

* a ``@stage`` inside a string literal or a comment is one token to the lexer and
  never a ``stageReference`` node, so it cannot become a false positive;
* ``@@version`` is the engine's ``systemVariable`` (two ``AT`` tokens), never a
  stage;
* the slash path ``@stage1/folder/x.csv`` and the glob ``@stage1.data/*.csv``
  are a single ``stageReference`` node, so the reference is detected whole
  instead of leaving a dangling ``/folder/x.csv`` / ``*.csv`` fragment that
  corrupts the SQL after translation.

This module owns only the *parse*: the translator and the credential injector
stay Nova's (``translator.py``, ``sql_pipeline.py``).

``LIST`` and ``COPY INTO`` are the two Nova surfaces the vendored StarRocks 4.1
grammar does not model (``LIST`` does not exist in StarRocks; ``COPY INTO`` is
4.2+). They are recognised by a **token scan of the statement prefix** — never a
regex over the text — and their stage references still come from the grammar's
``stageReference`` rule wherever the grammar can see them. The recovery is
documented on :func:`_nova_surface_stage_refs`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from antlr4 import CommonTokenStream, InputStream, Token
from antlr4.error.ErrorListener import ErrorListener

from app.sql_dialect.grammar import StarRocksLexer, StarRocksParser


class CommandType(Enum):
    """Types of SQL commands that can contain @stage references."""

    STAGE_QUERY = "stage_query"  # SELECT FROM @stage
    STAGE_BROWSE = "stage_browse"  # LIST FILES @stage
    STAGE_LOAD = "stage_load"  # COPY INTO table FROM @stage
    STAGE_EXPORT = "stage_export"  # COPY INTO @stage FROM table
    REGULAR = "regular"  # No @stage references


class UnsupportedStageCommandError(ValueError):
    """A stage command Nova recognises but cannot execute.

    ``LIST`` is parsed as :attr:`CommandType.STAGE_BROWSE` because the syntax is
    documented (``docs/02-sql-worksheet.md``) and the enum names it, but there is
    no implementation behind it and StarRocks has no ``LIST`` statement — every
    form of it is a syntax error at the engine. Raising here is what keeps the
    documented-but-unimplemented form from reaching the engine as the *user's*
    text and coming back as a StarRocks syntax error that names neither Nova nor
    the missing feature.
    """


class StageParseError(ValueError):
    """A syntax error carrying the engine's exact ``line:col`` position.

    ``str(error)`` is ``"<line>:<col> <message>"`` — the position prefix is what
    lets a caller point at *where* the statement is malformed instead of a bare
    "parse failed". The message never contains a credential: it is produced
    before any translation or injection runs.
    """

    def __init__(self, line: int, column: int, message: str) -> None:
        self.line = line
        self.column = column
        self.message = message
        super().__init__(f"{line}:{column} {message}")


@dataclass
class StageReference:
    """Parsed @stage reference from SQL."""

    full_match: str  # Original text: @stage1.data.csv
    stage_name: str  # Stage name: stage1
    path_parts: list[str]  # Remaining path: ['data', 'csv']
    file_name: str | None  # Last part if it looks like a file: 'data.csv'
    is_directory: bool  # True if no file extension
    original_text: str  # The full SQL text for context


@dataclass
class ParsedSQL:
    """Result of parsing a SQL statement."""

    command_type: CommandType
    stage_refs: list[StageReference]
    original_sql: str
    base_sql: str  # SQL without @stage references (for translation)
    errors: list[StageParseError]


#: File extensions that name a file rather than a directory segment.
_FILE_EXTENSIONS = {
    "csv",
    "tsv",
    "json",
    "parquet",
    "orc",
    "avro",
    "txt",
    "gz",
    "bz2",
    "snappy",
    "zstd",
    "lzo",
    "xlsx",
    "xls",
    "xml",
    "log",
    "sql",
    "ndjson",
    "jsonl",
}

#: The grammar's ``stageReference`` path separators, by token type. The token
#: text is compared, not the generated constant, so this survives a grammar
#: regeneration that renumbers the tokens.
_STAGE_SEPARATORS = frozenset({".", "/"})


class CaseInsensitiveInputStream(InputStream):
    """An ANTLR input stream whose keyword matching ignores case.

    Upstream StarRocks wraps its input in a Java ``CaseInsensitiveStream`` before
    lexing: the ``.g4`` keyword rules are uppercase literals (``SELECT:
    'SELECT';``), yet the engine accepts ``select``. The vendored Python target
    has no such wrapper, so without this the lexer rejects every lowercase
    keyword and a ``SELECT * FROM @stage1`` written in lower case fails to parse
    — a regression against the regex parser this slice replaces (109-A gaps this
    out; NOVA-126 carries it because it is the swap that would break).

    Only :meth:`LA` is folded, which is what the lexer matches on. ``getText``
    is inherited and reads ``strdata`` unchanged, so token text keeps the user's
    own casing: ``@Stage1`` and ``@stage1`` remain distinct identifiers, and the
    statement Nova forwards is byte-for-byte the user's.
    """

    def LA(self, offset: int):  # noqa: N802
        char = super().LA(offset)
        if 97 <= char <= 122:  # 'a'..'z'
            return char - 32
        return char


class _RecordingErrorListener(ErrorListener):
    """Collect syntax errors with their position instead of writing to stderr.

    ANTLR's default listener prints the offending text to stderr, which would
    leak statement content into a log; the caller decides what reaches the audit
    row instead.
    """

    def __init__(self) -> None:
        super().__init__()
        self.errors: list[StageParseError] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        self.errors.append(StageParseError(line, column, msg))


def _lex(sql: str) -> CommonTokenStream:
    """Lex ``sql`` into a token stream, dropping whitespace and comments.

    Channel choice is the lexer's: ``StarRocksLexer`` sends comments to the
    hidden channel, so ``token.text`` below is always live SQL. That is what
    makes a ``@stage`` in a comment invisible to the token scan, exactly as it
    is to the parser.
    """
    stream = CommonTokenStream(StarRocksLexer(CaseInsensitiveInputStream(sql)))
    stream.fill()
    return stream


def _visible_tokens(stream: CommonTokenStream) -> list:
    """The default-channel tokens of ``stream`` in source order.

    Whitespace and comments sit on the hidden channel (``StarRocksLexer`` sends
    them there), so filtering on the channel is what makes the token list read
    like live SQL: ``COPY INTO`` is two adjacent tokens, and a ``@stage`` in a
    comment never appears at all.
    """
    return [
        token
        for token in stream.tokens
        if token.channel == Token.DEFAULT_CHANNEL
        and token.type not in (Token.EOF, Token.INVALID_TYPE)
    ]


def _parse_tree(sql: str):
    """Lex and parse ``sql``; return ``(stream, tree, errors)``.

    One token stream is shared by lexer and parser, so a node's start token and
    ``stream.get(index)`` are the same object (``CommonTokenStream.get`` maps an
    index into that stream's own token list).
    """
    stream = _lex(sql)
    parser = StarRocksParser(stream)
    listener = _RecordingErrorListener()
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    tree = parser.sqlStatements()
    return stream, tree, listener.errors


def _walk_nodes(tree):
    """Every node in ``tree``, root first, in pre-order."""

    def walk(node):
        yield node
        for child in getattr(node, "children", None) or []:
            yield from walk(child)

    yield from walk(tree)


def _find_stage_atoms(tree) -> list:
    """Every ``StageAtomContext`` in ``tree``, in source order.

    A plain recursive walk rather than a visitor: the registry only needs the
    nodes, and this keeps the dependency surface to the generated node types.
    """
    return [node for node in _walk_nodes(tree) if type(node).__name__ == "StageAtomContext"]


def _decimal_atom_parts(atom) -> list[str]:
    """The path segments carried by one ``decimalAtom``, dot prefixes stripped.

    A ``decimalAtom`` is the grammar's stand-in for a ``stageSeparator
    stageSegment`` pair the lexer fused into a single ``DECIMAL_VALUE`` (NOVA-132):
    ``.2024`` is one token that already contains the ``.`` separator, so the
    atom stands where a separator would. It is also reachable as a plain
    ``stagePathAtom`` for the hyphen case (``@stage-2.`` arrives as ``2.``).

    ``DECIMAL_VALUE`` text is split on ``.`` so the *segments* come out (not the
    glued token): ``.2024`` → ``['2024']``, ``2.`` → ``['2']``. A nested
    ``stagePathAtom`` from the hyphen form (``2.`` then ``data``) is appended as
    the segment that follows, since the token itself already ended in a dot.
    Empty pieces are dropped, so a leading dot is a separator and nothing more.
    """
    parts: list[str] = []
    for child in atom.children or []:
        name = type(child).__name__
        if name == "TerminalNodeImpl":
            parts.extend(piece for piece in child.getText().split(".") if piece)
        elif name == "StagePathAtomContext":
            parts.extend(piece for piece in _stage_path_atom_parts(child) if piece)
    return parts


def _stage_path_atom_parts(atom) -> list[str]:
    """The path segments of one ``stagePathAtom``, decimals expanded recursively.

    ``stagePathAtom`` is ``identifier | * | INTEGER_VALUE | decimalAtom``, so a
    decimal nested here (``decimalAtom`` → ``DECIMAL_VALUE`` → ``stagePathAtom``)
    keeps expanding until the leaves are real segment text.
    """
    for child in atom.children or []:
        name = type(child).__name__
        if name == "DecimalAtomContext":
            return _decimal_atom_parts(child)
    return [atom.getText()]


def _decimal_atom_glue(atom) -> tuple[list[str], list[str]]:
    """Split one ``decimalAtom`` into the segments it closes and the ones it opens.

    The lexer fuses a ``.`` separator onto a neighbouring number, so a
    ``DECIMAL_VALUE`` token can carry separators inside it: ``.2024`` (leading,
    ``@stage1.2024.csv``), ``2.`` (trailing, ``@stage-2.data.csv``) or ``2.2024``
    (both, ``@stage-2.2024.csv``). Every ``.`` in that token is a
    ``stageSeparator``, so the pieces are segments: the first piece continues the
    current segment (``stage-`` + ``2`` → ``stage-2``), and each later piece opens
    a new one. That is the split the pre-swap regex parser produced, so
    ``@stage-2.2024.csv`` reads as ``stage-2`` then ``2024`` then ``csv``.

    A nested ``stagePathAtom`` after a trailing dot (``2.`` then ``data``) opens
    the next segment too, and one reached without a trailing dot is appended to
    the segment currently open.

    Returns ``(closed, opened)`` where either list may be empty.
    """
    pieces: list[str] = []
    opened: list[str] = []

    for child in atom.children or []:
        name = type(child).__name__
        if name == "TerminalNodeImpl":
            pieces.extend(piece for piece in child.getText().split(".") if piece)
        elif name == "StagePathAtomContext":
            tail = _stage_path_atom_parts(child)
            if pieces:
                opened.extend(tail)
            else:
                pieces.extend(tail)

    closed = pieces[:1]
    opened = pieces[1:] + opened
    return closed, opened


def _stage_segment_parts(segment) -> list[str]:
    """The path segments of one ``stageSegment`` node, splits included.

    ``stageSegment`` is ``stagePathAtom (MINUS_SYMBOL stagePathAtom)*``, so the
    default reading is one segment spelled across hyphens (``daily-load-2``). A
    trailing-dot decimal inside it is a separator, though: ``stage-2.data`` is two
    segments because the ``2.`` token carries the ``.`` (NOVA-109 review). This
    walks the atoms in source order and starts a new segment wherever that dot
    lands, so the hyphen form segments exactly as the pre-swap regex parser did.
    """
    segments: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            segments.append("".join(current))
            current.clear()

    for child in segment.children or []:
        name = type(child).__name__
        if name == "StagePathAtomContext":
            closed, opened = _stage_path_atom_glue(child)
            current.extend(closed)
            if opened:
                flush()
                current.extend(opened)
        else:
            # ``MINUS_SYMBOL`` -- a literal within the segment, not a break.
            current.append(child.getText())

    flush()
    return segments


def _stage_path_atom_glue(atom) -> tuple[list[str], list[str]]:
    """``(closed, opened)`` segment pieces for one ``stagePathAtom``.

    Delegates to :func:`_decimal_atom_glue` when the atom wraps a ``decimalAtom``
    (the fused-token case); otherwise the atom is literal text for the current
    segment and opens nothing.
    """
    for child in atom.children or []:
        if type(child).__name__ == "DecimalAtomContext":
            return _decimal_atom_glue(child)
    return _stage_path_atom_parts(atom), []


def _stage_reference_from_atom(atom, original_sql: str) -> StageReference:
    """Build a :class:`StageReference` from one ``#stageAtom`` node.

    The grammar composes the reference as ``AT stageSegment (stageSeparator
    stageSegment | fusedDecimal)* '/'?`` (``StarRocks.g4``, NOVA-BEGIN block). The
    first segment is the stage name; the rest are path segments in the order
    written, whether joined by ``.`` or ``/``. Reassembling from the *ordered*
    children — not from a regex, and not from ``getText()`` alone — is what makes
    the slash path and the glob arrive intact: ``*`` and ``csv`` are two segments
    of ``@stage1.data/*.csv``, so the file is ``*.csv`` and the translated path
    keeps the glob rather than dropping it.

    The fused decimals matter for the NOVA-132 regression: the dotted numeric
    form (``@stage1.2024.csv``) puts the fused ``.2024`` next to the
    ``stageSegment``s rather than wrapping it in one, so reading only
    ``stageSegment()`` would lose every numeric path segment and mis-split the
    file name (``['csv']`` instead of ``['2024', 'csv']``). Walking the children
    in source order and expanding each fused decimal restores the segment list
    the regex parser produced on ``main``. ``fusedDecimal`` is the separator
    position (one segment, never absorbs a following atom); ``decimalAtom`` is
    reachable inside a ``stageSegment`` for the trailing-dot hyphen form.
    """
    stage_ref = atom.stageReference()

    segments: list[str] = []
    for child in stage_ref.children or []:
        name = type(child).__name__
        if name == "StageSegmentContext":
            # A hyphen-joined segment, split where a trailing-dot decimal hid a
            # ``.`` separator inside it (``stage-2.data`` -> two segments).
            segments.extend(_stage_segment_parts(child))
        elif name in ("FusedDecimalContext", "DecimalAtomContext"):
            segments.extend(_decimal_atom_parts(child))

    if not segments:
        # No path atoms beyond a malformed reference; fall back to the token
        # text so the caller still gets a name rather than an IndexError.
        text = stage_ref.getText()
        segments = [text.lstrip("@")]

    stage_name = segments[0]
    raw_parts = segments[1:]

    return _build_reference(
        full_match=stage_ref.getText(),
        stage_name=stage_name,
        raw_parts=raw_parts,
        original_sql=original_sql,
    )


def _build_reference(
    *,
    full_match: str,
    stage_name: str,
    raw_parts: list[str],
    original_sql: str,
) -> StageReference:
    """Assemble a :class:`StageReference` from a name plus path segments.

    The file/directory split is the translator's contract (``path_parts`` +
    ``file_name`` feed ``build_s3_path``), so it is computed here once for both
    the ANTLR4 path and the Nova-surface token path.

    A trailing ``*`` (a glob segment) is a *directory* marker, not a file: the
    reference names every file under a prefix. Treating ``*`` as the last part
    would make ``file_name`` depend on the segment after the separator, which is
    what produced the dangling ``FILES(...)/*.csv``.
    """
    file_name = None
    path_parts = list(raw_parts)

    if raw_parts:
        last = raw_parts[-1]
        if last == "*":
            # Glob: the directory is everything before the star, and there is no
            # single file to name — the extension after the star is the filter.
            file_name = None
            path_parts = raw_parts[:-1]
        elif last.lower() in _FILE_EXTENSIONS:
            # File detected: e.g. ['data', 'csv'] → file_name='data.csv',
            # path_parts=['data'].
            if len(raw_parts) >= 2:
                file_name = f"{raw_parts[-2]}.{raw_parts[-1]}"
                path_parts = raw_parts[:-2]  # Everything except the file
            else:
                # Just an extension like '.csv' — treat as directory
                file_name = None
                path_parts = raw_parts
        # Otherwise the last part is a directory name, and the defaults stand.

    return StageReference(
        full_match=full_match,
        stage_name=stage_name,
        path_parts=path_parts,
        file_name=file_name,
        is_directory=file_name is None,
        original_text=original_sql,
    )


# ---------------------------------------------------------------------------
# Command classification
# ---------------------------------------------------------------------------

#: Statement node kinds whose first token is the whole discriminator. The
#: grammar already proved the shape, so membership is a classification, never a
#: validation. ``SingleStatementContext`` is the outermost wrapper every
#: statement carries; the *inner* concrete node names the command, so the
#: classifier reads the deepest one.
_QUERY_STATEMENTS = frozenset(
    {
        "QueryStatementContext",
        "ExplainStatementContext",
        "DescStatementContext",
        "ShowStmtContext",
        "ShowFunctionsStatementContext",
    }
)

_BROWSE_STATEMENTS = frozenset({"ShowStagesStatementContext"})

#: Tokens that may sit between ``COPY INTO`` and the operand it introduces.
_COPY_INTO_NOISE = frozenset({"TABLE", "FILES", "IF"})


#: Wrapper nodes that only carry the statement; the concrete command node is
#: their child. Classification must skip past them or every statement reads as
#: ``SingleStatementContext``.
_STATEMENT_WRAPPERS = frozenset({"SingleStatementContext", "StatementContext"})


def _first_statement(tree) -> object:
    """The first concrete statement node of ``tree``, or ``tree`` itself.

    ``sqlStatements`` wraps each statement in a ``SingleStatementContext`` and a
    ``StatementContext``; both are generic, so the classifier walks past them to
    the node that names the command (``QueryStatementContext``,
    ``ExplainStatementContext``, …).
    """
    fallback = tree
    for node in _walk_nodes(tree):
        name = type(node).__name__
        if name.endswith("StatementContext") and name not in _STATEMENT_WRAPPERS:
            return node
        if name.endswith("StatementContext"):
            fallback = node
    return fallback


def _texts(tokens: list) -> list[str]:
    return [token.text.upper() for token in tokens]


def detect_command_type(tree, token_texts: list[str]) -> CommandType:
    """Detect the type of SQL command from ``tree`` and its leading token texts.

    The tree has been validated by the grammar, so the statement *kind* decides
    the engine surfaces. The two Nova-only surfaces (``LIST``, ``COPY INTO``) do
    not exist in the StarRocks grammar, so their statements parse as
    ``ErrorStatementContext`` and are classified from the leading tokens.
    ``token_texts`` is the visible-token prefix of the statement, supplied by
    :func:`parse_sql`; comparing text (not generated token constants) keeps the
    check stable across a grammar regeneration.
    """
    kind = type(_first_statement(tree)).__name__

    if token_texts[:1] == ["COPY"] and token_texts[1:2] == ["INTO"]:
        # Direction decides load vs export. ``COPY INTO @stage`` is an export;
        # the reference itself comes from the registry, so the ``@`` operand is
        # never guessed here.
        return CommandType.STAGE_LOAD

    if token_texts[:1] == ["LIST"]:
        return CommandType.STAGE_BROWSE

    if kind in _QUERY_STATEMENTS:
        return CommandType.STAGE_QUERY

    if kind in _BROWSE_STATEMENTS:
        return CommandType.STAGE_BROWSE

    return CommandType.REGULAR


def _direction_from_tokens(token_texts: list[str]) -> CommandType:
    """Load vs export for ``COPY INTO``, read from the token order.

    The stage's side of the statement is the operand position itself: ``FROM
    @stage`` is a load, ``INTO @stage`` an export. Both spellings appear in the
    token list, so the operator nearest ``@`` wins.
    """
    for index, text in enumerate(token_texts):
        if text == "@":
            before = token_texts[max(0, index - 4) : index]
            if "INTO" in before and "FROM" not in before:
                return CommandType.STAGE_EXPORT
            return CommandType.STAGE_LOAD
    return CommandType.STAGE_LOAD


# ---------------------------------------------------------------------------
# Nova-surface recovery (LIST / COPY INTO)
# ---------------------------------------------------------------------------


def _nova_surface_stage_refs(sql: str) -> list[StageReference]:
    """Stage references in a Nova surface the StarRocks grammar cannot parse.

    ``LIST`` and ``COPY INTO`` are Nova commands with no rule in StarRocks 4.1.
    They are recognised by scanning the **token stream** of the statement for
    the grammar's own ``stageReference`` shape, so the reference grammar is
    still the single source of truth:

    * an ``@`` token begins a reference only when it is not the second ``@`` of
      a ``@@...`` system variable (``@@version`` stays a variable);
    * a reference runs over ``stageSegment`` atoms — an identifier, ``*``, an
      integer, or a decimal — joined by ``.`` / ``/``;
    * the scan only starts after the surface keyword, so a variable operand such
      as ``COPY INTO t (a) VALUES (@x)`` is never claimed.

    Comments and literals are already off the token stream, so a ``@stage`` in
    ``'FROM @x'`` or ``-- @x`` cannot appear here.

    Known gap (NOVA-136): this scan reads ``@ atom (sep atom)*`` only, not the
    grammar's ``stageSegment : stagePathAtom (MINUS_SYMBOL stagePathAtom)*``
    alternative, so a hyphenated name such as ``@stage-2.data.csv`` stops at the
    hyphen on a Nova surface. The FROM-table path is correct (it reads the tree);
    only ``LIST``/``COPY INTO`` are affected.
    """
    tokens = _visible_tokens(_lex(sql))
    for index, token in enumerate(tokens):
        if token.text.upper() in ("LIST", "COPY"):
            tokens = tokens[index:]
            break

    refs: list[StageReference] = []
    index = 0
    while index < len(tokens):
        if tokens[index].text != "@":
            index += 1
            continue

        # ``@stage1.data/*`` is a glob: the ``*`` is a path segment and the
        # scan continues so a following ``.csv``/``.parquet`` joins it rather
        # than being left dangling.
        ref = _reference_from_tokens(tokens, index, sql)
        if ref is None:
            index += 1
            continue
        refs.append(ref)

        # Advance past this reference's span so an inner ``@@`` cannot be
        # re-read, and so a bare ``@`` that started a system variable is not
        # revisited.
        stop = tokens[index].start + len(ref.full_match)
        index += 1
        while index < len(tokens) and tokens[index].start < stop:
            index += 1
    return refs


#: Token types whose text is a numeric path segment the lexer fused the leading
#: ``.`` separator into: ``.2024`` → ``DECIMAL_VALUE``, ``.2024_01`` →
#: ``DOT_IDENTIFIER``, ``.2e3`` → ``DOUBLE_VALUE`` (NOVA-132). The grammar's
#: ``fusedDecimal``/``decimalAtom`` accept all three; the token scan has to as
#: well, or the Nova surfaces (``LIST``/``COPY INTO``) would stop the reference at
#: the stage name and leave ``.2024.csv`` dangling.
_FUSED_DECIMAL_TOKENS = frozenset(
    {
        StarRocksLexer.DECIMAL_VALUE,
        StarRocksLexer.DOT_IDENTIFIER,
        StarRocksLexer.DOUBLE_VALUE,
    }
)


def _fused_decimal_parts(token) -> list[str] | None:
    """Split a fused leading-dot numeric token into its path segments, or ``None``.

    ``DECIMAL_VALUE`` text comes in two shapes on this surface: leading (``.2024``
    — separator first, ``['2024']``) and trailing (``2.`` — separator last, after
    a hyphen, ``['2']``). ``DOT_IDENTIFIER`` is always leading (``.2024_01``), and
    ``DOUBLE_VALUE`` is the exponent form (``.2e3`` → ``['2e3']``). The split
    drops empty pieces, so the ``.`` is a separator and never a segment. Returns
    ``None`` when the token is not one of the fused numeric types.
    """
    if token.type not in _FUSED_DECIMAL_TOKENS:
        return None
    return [piece for piece in token.text.split(".") if piece]


def _is_segment_atom(token) -> bool:
    """Whether ``token`` can be one ``stageSegment`` atom.

    Mirrors the grammar's ``stagePathAtom``: an identifier (including a
    backquoted one), ``*``, an integer, or a decimal (``DECIMAL_VALUE`` /
    ``DOT_IDENTIFIER`` / ``DOUBLE_VALUE``). A keyword such as ``FROM`` parses as
    an identifier in the grammar, and the token type for the literal ``*`` is the
    same wherever it appears, so the test is on token text.
    """
    if token.text == "*":
        return True
    if token.type in (
        StarRocksLexer.INTEGER_VALUE,
        StarRocksLexer.DECIMAL_VALUE,
        StarRocksLexer.DOT_IDENTIFIER,
        StarRocksLexer.DOUBLE_VALUE,
    ):
        return True
    return _is_identifier_token(token)


def _is_identifier_token(token) -> bool:
    """Whether ``token`` is an identifier (bare, backquoted, or a keyword).

    StarRocks keywords are usable as identifiers, so the grammar's
    ``identifier`` rule accepts the reserved words too; the lexer tags them by
    keyword type. A token is an identifier when it is not a punctuation /
    operator / literal — checked structurally on the accepted character set,
    which is exactly what ``identifier`` allows (``[A-Za-z_][A-Za-z0-9_$]*``).
    """
    text = token.text
    if not text:
        return False
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        return True
    head = text[0]
    if not (head.isalpha() or head == "_"):
        return False
    return all(char.isalnum() or char in "_$" for char in text)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_sql(sql: str) -> ParsedSQL:
    """Parse SQL and extract all @stage references.

    References come from the ANTLR4 parse tree: every ``StageAtomContext`` the
    vendored grammar produced is one ``StageReference``. There is no
    ``_STAGE_PATTERN`` / ``_AT_TOKEN`` candidate scan over the raw text and
    therefore nothing that can drift from the grammar.

    The two Nova surfaces the grammar does not model (``LIST``, ``COPY INTO``)
    fall back to :func:`_nova_surface_stage_refs`, which scans the *token
    stream* for the same reference shape.

    A syntax error is recorded in ``ParsedSQL.errors`` with its exact
    ``line:col`` and the stage registry is left empty, so a statement Nova could
    not parse travels the ordinary path and the engine answers with its own
    error rather than Nova guessing at a rewrite from a partial tree.

    ``LIST`` is a special case. Nova parses it as
    :attr:`CommandType.STAGE_BROWSE` (the documented surface), but nothing
    implements it and the engine has no ``LIST`` statement, so a ``LIST`` that
    reaches execution always fails; it is never translated.

    Raises:
        UnsupportedStageCommandError: never today; reserved for the caller that
            decides to refuse ``LIST`` outright rather than pass it on.
    """
    if not sql.strip():
        return ParsedSQL(
            command_type=CommandType.REGULAR,
            stage_refs=[],
            original_sql=sql,
            base_sql=sql,
            errors=[],
        )

    if "@" not in sql:
        # Invariant this short-circuit relies on: every stage form the dialect
        # acts on carries a literal ``@`` — ``@stage1``, ``@stage1/``, the dotted
        # ``@stage1.data.csv``, the glob ``@stage1.data/*.csv``, and the ``LIST``
        # / ``COPY INTO`` Nova surfaces. A statement with no ``@`` at all cannot
        # contain a stage, so ``stage_refs=[]`` and ``REGULAR`` are the grammar's
        # own answer and building the ANTLR4 tree would only re-derive it at a
        # ~50x p95 cost on the request path (AC-5 / NOVA-17). The test is the
        # literal character, not a regex: anything subtler reintroduces a
        # text-pattern source of truth, which is exactly what 109-B removes.
        return ParsedSQL(
            command_type=CommandType.REGULAR,
            stage_refs=[],
            original_sql=sql,
            base_sql=sql,
            errors=[],
        )

    stream, tree, errors = _parse_tree(sql)
    leading = _texts(_visible_tokens(stream)[:4])

    nova_surface = leading[:1] in (["LIST"], ["COPY"])
    if errors and not nova_surface:
        # A partial tree must not drive a rewrite: a missing token can make a
        # later ``@stage`` parse as something else, and the translator would then
        # rewrite the user's statement into a credential-bearing ``FILES()`` call
        # that is not the statement they wrote.
        return ParsedSQL(
            command_type=CommandType.REGULAR,
            stage_refs=[],
            original_sql=sql,
            base_sql=sql,
            errors=errors,
        )

    command_type = detect_command_type(tree, leading)

    if nova_surface:
        stage_refs = _nova_surface_stage_refs(sql)
        if leading[1:2] == ["INTO"]:
            command_type = _direction_from_tokens(_all_token_texts(stream))
    else:
        stage_refs = [_stage_reference_from_atom(atom, sql) for atom in _find_stage_atoms(tree)]

    # A ``LIST``/``COPY`` with no stage reference is not a stage command at all.
    # Falling back to REGULAR (rather than keeping STAGE_BROWSE) routes it down
    # the ordinary path, where the engine answers its own syntax error naming the
    # keyword. That is not silent: the statement is passed through untouched and
    # fails visibly, which is the honest outcome for a documented command Nova
    # has no implementation for.
    if not stage_refs:
        command_type = CommandType.REGULAR

    return ParsedSQL(
        command_type=command_type,
        stage_refs=stage_refs,
        original_sql=sql,
        base_sql=sql,
        errors=[],
    )


def _all_token_texts(stream: CommonTokenStream) -> list[str]:
    """Every visible token text of ``stream``, upper-cased."""
    return _texts(_visible_tokens(stream))


# ---------------------------------------------------------------------------
# Helpers for the MySQL proxy's session-variable substitution
# ---------------------------------------------------------------------------


def _token_index_at(tokens: list, position: int) -> int | None:
    """The index in ``tokens`` of the ``@`` token starting at ``position``."""
    for index, token in enumerate(tokens):
        if token.start == position:
            return index if token.text == "@" else None
        if token.start > position:
            return None
    return None


def _reference_from_tokens(tokens: list, start_index: int, sql: str) -> StageReference | None:
    """The reference shape beginning at ``tokens[start_index]``, or ``None``.

    Shared by the Nova-surface scan and :func:`stage_reference_at`, so the
    proxy's decision to leave a reference alone is made by the same token logic
    that builds the engine's registry.

    A numeric path segment whose leading ``.`` the lexer fused into the token
    (``@stage1.2024.csv`` → ``stage1`` ``.2024`` ``.`` ``csv``, or the
    ``.2024_01`` ``DOT_IDENTIFIER``) carries its separator *inside* the token, so
    it continues the reference directly — exactly as the grammar's ``decimalAtom``
    glue does (NOVA-132). A bare ``.`` / ``/`` separator token continues it as
    before.
    """
    if start_index >= len(tokens) or tokens[start_index].text != "@":
        return None
    if start_index + 1 < len(tokens) and tokens[start_index + 1].text == "@":
        return None

    index = start_index + 1
    if index >= len(tokens) or not _is_segment_atom(tokens[index]):
        return None

    segments = [tokens[index].text]
    index += 1
    while index < len(tokens):
        fused = _fused_decimal_parts(tokens[index])
        if fused is not None:
            # The separator is the fused token's own leading dot: the pieces are
            # path segments and the reference continues.
            segments.extend(fused)
            index += 1
            continue
        separator = tokens[index]
        if separator.text not in _STAGE_SEPARATORS or index + 1 >= len(tokens):
            break
        following = tokens[index + 1]
        if following.text == "@" or not _is_segment_atom(following):
            break
        segments.append(following.text)
        index += 2

    if index < len(tokens) and tokens[index].text == "/":
        index += 1

    full_match = sql[tokens[start_index].start : tokens[index - 1].stop + 1]
    return _build_reference(
        full_match=full_match,
        stage_name=segments[0],
        raw_parts=segments[1:],
        original_sql=sql,
    )


def stage_reference_at(sql: str, position: int) -> StageReference | None:
    """The stage reference beginning at ``position`` in ``sql``, or ``None``.

    The MySQL proxy substitutes session variables into a statement before it
    reaches the dialect engine, and it must not substitute inside a stage
    reference (the two are spelled identically and only position separates them).
    Rather than a second classifier that could disagree with the engine, the
    proxy asks this module, which answers from the same token logic the registry
    is built from — and then confirms against the parse tree, so a variable
    operand such as ``SELECT @x`` is never claimed as a stage:

    1. the token at ``position`` must be an ``@`` (not the second ``@`` of a
       ``@@`` system variable);
    2. the token stream must form the grammar's reference shape at that point;
    3. for a statement the grammar parses, the tree must contain a
       ``stageReference`` node whose span starts exactly at ``position`` — which
       is what rules out ``SELECT @x``, where the ``@`` is a ``systemVariable``
       operand and no reference node exists.

    A Nova surface (``LIST``/``COPY INTO``), which the grammar cannot parse,
    skips step 3: its reference is recognised from the token shape alone.
    """
    if position < 0 or position >= len(sql) or sql[position] != "@":
        return None
    if position + 1 < len(sql) and sql[position + 1] == "@":
        return None

    tokens = _visible_tokens(_lex(sql))
    start_index = _token_index_at(tokens, position)
    if start_index is None:
        return None

    ref = _reference_from_tokens(tokens, start_index, sql)
    if ref is None:
        return None

    stream, tree, errors = _parse_tree(sql)
    leading = _texts(_visible_tokens(stream)[:1])
    if errors and leading not in (["LIST"], ["COPY"]):
        return None
    if leading in (["LIST"], ["COPY"]):
        return ref

    for atom in _find_stage_atoms(tree):
        parsed = _stage_reference_from_atom(atom, sql)
        if token_span_start(atom) == position:
            return parsed
    return None


def token_span_start(atom) -> int:
    """Character offset of a ``#stageAtom``'s stage reference."""
    return atom.stageReference().start.start
