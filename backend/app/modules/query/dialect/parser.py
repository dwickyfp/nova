"""@stage SQL dialect — parse, translate, inject credentials, detect formats.

Nova's custom SQL dialect: @stage_name.path.file.csv
Translates to StarRocks FILES() function with auto-detected format + injected credentials.

Examples:
    SELECT * FROM @stage1.data.csv
    → SELECT * FROM FILES('path'='s3://bucket/prefix/data.csv', 'format'='csv', creds...)

    SELECT * FROM @silver.stage1.folder.file.parquet
    → SELECT * FROM FILES('path'='s3://bucket/prefix/folder/file.parquet', 'format'='parquet', creds...)
"""

import re
from dataclasses import dataclass
from enum import Enum


class CommandType(Enum):
    """Types of SQL commands that can contain @stage references."""
    STAGE_QUERY = "stage_query"      # SELECT FROM @stage
    STAGE_BROWSE = "stage_browse"    # LIST FILES @stage
    STAGE_LOAD = "stage_load"        # COPY INTO table FROM @stage
    STAGE_EXPORT = "stage_export"    # COPY INTO @stage FROM table
    REGULAR = "regular"              # No @stage references


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


@dataclass
class StageReference:
    """Parsed @stage reference from SQL."""
    full_match: str          # Original text: @stage1.data.csv
    stage_name: str          # Stage name: stage1
    path_parts: list[str]    # Remaining path: ['data', 'csv']
    file_name: str | None    # Last part if it looks like a file: 'data.csv'
    is_directory: bool       # True if no file extension
    original_text: str       # The full SQL text for context


@dataclass
class ParsedSQL:
    """Result of parsing a SQL statement."""
    command_type: CommandType
    stage_refs: list[StageReference]
    original_sql: str
    base_sql: str  # SQL without @stage references (for translation)


# A stage reference is ``@name`` plus an optional dotted path and directory
# slash: ``@stage1``, ``@stage1/``, ``@stage1.data.csv``,
# ``@silver.stage1.folder.file.parquet``.
#
# The leading ``(?<!@)`` excludes ``@@name``, a *system* variable. Every client
# asks for ``@@version_comment`` on connect, and without the lookbehind the
# pattern matched the second ``@`` and read a stage called ``version_comment``,
# so every MySQL client's login sequence failed before it sent a user query.
#
# The dotted path is optional again (``*``, not ``+``): a bare ``@stage1`` and a
# directory ``@stage1/`` are documented stage forms
# (``docs/04-stage-manager.md:87``, ``docs/GUIDE_OBJECTS.md:128``), and requiring
# a dot silently disabled stage browsing. What separates a stage from a *user
# variable* is **context**, not the presence of a dot — see
# :func:`_classify_at_token`.
_STAGE_PATTERN = re.compile(
    r'(?<!@)@([a-zA-Z_][a-zA-Z0-9_-]*)'  # stage name (letters, digits, underscores, hyphens)
    r'((?:\.[a-zA-Z0-9_-]+)*)'        # optional .path.parts (supports hyphens)
    r'(/)?'                           # optional trailing slash (directory)
    r'(?:\s|$|;|,|\)|\()'            # boundary
)

#: Keywords after which a bare ``@name`` denotes a stage rather than a variable.
#:
#: ``FROM``/``JOIN``/``INTO`` introduce a table-position operand and ``LIST``
#: takes a stage as its whole argument. ``COPY INTO @stage FROM table`` puts the
#: stage after ``INTO``, and a load puts it after ``FROM``; both are covered by
#: this set.
_STAGE_CONTEXT_KEYWORDS = frozenset(
    {"FROM", "JOIN", "INTO", "LIST", "FILES", "USING"}
)

#: Characters after which ``@name`` is unambiguously a variable operand: an
#: operator, an opening paren of a function call, or a comma in an expression
#: list. ``SELECT @x``, ``WHERE a > @x`` and ``1 + @n`` all land here.
_EXPRESSION_PRECEDERS = frozenset("+-*/%<>=!|&^,(~")

#: Tokens that may sit between a stage keyword and the ``@name`` it introduces,
#: e.g. ``LIST FILES @stage1`` or ``COPY INTO table FROM @stage``.
_QUALIFIERS = frozenset({"FILES", "ALL", "DISTINCT"})

#: Matches one ``@name`` plus its optional dotted path and slash, without the
#: trailing boundary requirement — used by the tokenizer, which needs to see the
#: token in its surrounding context rather than requiring a delimiter.
_AT_TOKEN = re.compile(
    r'(?<!@)@(?P<name>[a-zA-Z_][a-zA-Z0-9_-]*)(?P<path>(?:\.[a-zA-Z0-9_-]+)*)(?P<slash>/?)'
)


def _classify_at_token(sql: str, match: re.Match) -> bool:
    """Whether the ``@name`` at ``match`` is a stage reference.

    A stage and a user variable are spelled identically — ``@x`` in
    ``SELECT @x`` and ``@stage1`` in ``SELECT * FROM @stage1`` — so the
    *position* decides, not the name:

    * a dotted path or a trailing ``/`` is always a stage
      (``@stage1.data.csv``, ``@stage1/``);
    * otherwise the token is a stage only when a stage-introducing keyword is
      the nearest preceding significant token (``FROM @stage1``,
      ``LIST FILES @stage1``), optionally through a qualifier such as ``FILES``;
    * everywhere else — after an operator, a comma, an opening paren, or at the
      head of an expression — it is a variable operand.

    This is what keeps ``SELECT @x``, ``1 + @n`` and ``SET @my_stage = 1`` out
    of the stage path while ``SELECT * FROM @stage1`` stays in it. The previous
    revision distinguished them by requiring a dot, which also rejected the
    documented bare and directory forms and silently disabled ``LIST``.
    """
    if match.group("path") or match.group("slash"):
        return True

    preceding = _preceding_significant_token(sql, match.start())
    if preceding is None:
        # Nothing before the token: ``@stage1/`` is handled above, so a bare
        # leading ``@name`` has no stage context to justify it.
        return False

    word, kind = preceding
    if kind == "ident" and word.upper() in _QUALIFIERS:
        # ``LIST FILES @stage1`` — step back past the qualifier.
        return _nearest_keyword_is_stage_context(sql, match.start())
    if kind == "ident":
        return word.upper() in _STAGE_CONTEXT_KEYWORDS
    return False


def _preceding_significant_token(sql: str, position: int) -> tuple[str, str] | None:
    """The token immediately before ``position``, skipping whitespace/comments.

    Returns ``(text, kind)`` where ``kind`` is ``"ident"`` for a word and
    ``"punct"`` for anything else, or ``None`` at the start of the input.

    Comments are skipped rather than treated as the preceding token: the engine
    reads ``SELECT * FROM /* note */ @stage1`` as ``FROM @stage1``, so the
    classifier has to as well or a comment between the keyword and the reference
    flips it to a variable.

    ``_strip_comment_tail`` is applied to the text before the cursor first, so a
    ``--`` anywhere in the current line removes the rest of that line in one
    step. Detecting the marker lazily — only once the scan reached a newline —
    did not work: the scan returns on the first word it finds, which is comment
    text, before it ever looks at the newline.
    """
    trimmed = _strip_comment_tail(sql, position)
    index = len(trimmed) - 1
    while index >= 0:
        char = trimmed[index]
        if char.isspace():
            index -= 1
            continue
        # ``*/`` closes a block comment when scanning backwards.
        if char == "/" and index >= 1 and trimmed[index - 1] == "*":
            start = trimmed.rfind("/*", 0, index - 1)
            index = start - 1 if start >= 0 else -1
            continue
        if char in "`\"'":
            # A quoted identifier or string literal: consume the quoted run.
            quote = char
            end = index
            index -= 1
            while index >= 0 and trimmed[index] != quote:
                index -= 1
            kind = "punct" if quote == "'" else "ident"
            return trimmed[index + 1 : end + 1], kind
        if char.isalnum() or char in "_$":
            end = index
            while index >= 0 and (trimmed[index].isalnum() or trimmed[index] in "_$"):
                index -= 1
            return trimmed[index + 1 : end + 1], "ident"
        return char, "punct"
    return None


def _strip_comment_tail(sql: str, position: int) -> str:
    """``sql[:position]`` with every line-comment span removed.

    Removes both the comment that is still open at ``position`` and any earlier
    one, so the backward scan never stops on comment text. Simply cutting at the
    last ``--`` is not enough: ``FROM -- c\\n @stage1`` has the comment closed by
    its newline, and the text after it is live SQL whose preceding token is
    still ``FROM`` — cutting would either keep ``c`` or drop ``FROM``.
    """
    out: list[str] = []
    index = 0
    length = min(position, len(sql))
    while index < length:
        char = sql[index]

        if char == "'":
            # Copy the whole literal verbatim, honouring '' and \\' escapes, so
            # a ``--`` inside it is not mistaken for a comment.
            out.append(char)
            index += 1
            while index < length:
                literal = sql[index]
                out.append(literal)
                if literal == "\\" and index + 1 < length:
                    out.append(sql[index + 1])
                    index += 2
                    continue
                if literal == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        out.append("'")
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue

        if char == "-" and sql.startswith("--", index):
            newline = sql.find("\n", index)
            if newline < 0 or newline >= length:
                break  # the comment swallows the rest of the prefix
            out.append(" ")
            index = newline
            continue

        out.append(char)
        index += 1

    return "".join(out)


def _nearest_keyword_is_stage_context(sql: str, position: int) -> bool:
    """Walk back from ``position`` to the nearest word and test it.

    Used for the qualifier case: ``LIST FILES @stage1`` steps back over
    ``FILES`` to ``LIST``.
    """
    cursor = position
    for _ in range(3):
        token = _preceding_significant_token(sql, cursor)
        if token is None:
            return False
        text, kind = token
        if kind == "ident":
            return text.upper() in _STAGE_CONTEXT_KEYWORDS
        cursor -= len(text)
    return False

# File extension pattern
_FILE_EXTENSIONS = {
    'csv', 'tsv', 'json', 'parquet', 'orc', 'avro',
    'txt', 'gz', 'bz2', 'snappy', 'zstd', 'lzo',
    'xlsx', 'xls', 'xml', 'log', 'sql', 'ndjson', 'jsonl',
}


def parse_stage_reference(
    match: re.Match,
    original_sql: str,
    *,
    name: str | None = None,
    path: str | None = None,
) -> StageReference:
    """Build a :class:`StageReference` from a matched ``@name[.path][/]`` token.

    ``name`` and ``path`` are passed in by :func:`parse_sql`, which matches with
    :data:`_AT_TOKEN`; they default to the positional groups of
    :data:`_STAGE_PATTERN` so the function stays usable with either pattern.
    """
    stage_name = name if name is not None else match.group(1)
    path_str = path if path is not None else match.group(2)  # e.g. ".data.folder.file.csv"

    full_match = match.group(0).rstrip().rstrip(';').rstrip(',')
    raw_parts = [p for p in path_str.split('.') if p] if path_str else []

    # Determine if last part is a file (has known extension)
    file_name = None
    is_directory = True
    path_parts = raw_parts  # Default: all parts are path

    if raw_parts:
        # Check if last part is a known file extension
        last = raw_parts[-1].lower()
        if last in _FILE_EXTENSIONS:
            # File detected: e.g. ['data', 'csv'] → file_name='data.csv', path_parts=['data']
            # Or ['folder', 'file', 'parquet'] → file_name='file.parquet', path_parts=['folder', 'file']
            if len(raw_parts) >= 2:
                file_name = f"{raw_parts[-2]}.{raw_parts[-1]}"
                path_parts = raw_parts[:-2]  # Everything except the file
            else:
                # Just an extension like '.csv' — treat as directory
                file_name = None
                path_parts = raw_parts
        else:
            # Last part is not a known extension — it's a directory name
            file_name = None
            path_parts = raw_parts

    return StageReference(
        full_match=f"@{stage_name}{path_str}",
        stage_name=stage_name,
        path_parts=path_parts,
        file_name=file_name,
        is_directory=file_name is None,
        original_text=original_sql,
    )


def detect_command_type(sql: str) -> CommandType:
    """Detect the type of SQL command from the statement."""
    upper = sql.strip().upper()

    if re.match(r'^\s*(SELECT|WITH|SHOW|DESCRIBE|DESC|EXPLAIN)\b', upper):
        return CommandType.STAGE_QUERY
    if re.match(r'^\s*LIST\b', upper):
        return CommandType.STAGE_BROWSE
    if re.match(r'^\s*COPY\s+INTO\b', upper):
        # Check direction: COPY INTO table FROM @stage (load) or COPY INTO @stage FROM table (export)
        if re.search(r'FROM\s+@', upper):
            return CommandType.STAGE_LOAD
        if re.search(r'INTO\s+@', upper):
            return CommandType.STAGE_EXPORT

    return CommandType.REGULAR


def parse_sql(sql: str) -> ParsedSQL:
    """Parse SQL and extract all @stage references.

    References are found by :data:`_AT_TOKEN` and then classified by context
    (:func:`_classify_at_token`), because a stage and a user variable share a
    spelling and only their position tells them apart.

    ``LIST`` is a special case. Nova parses it as
    :attr:`CommandType.STAGE_BROWSE`, but nothing implements it and the engine
    has no ``LIST`` statement, so a ``LIST`` that reaches execution always
    fails. A ``LIST`` carrying a stage is reported as ``STAGE_BROWSE`` with its
    reference attached — the caller can then refuse it knowingly — and a bare
    ``LIST`` is reported as ``REGULAR`` with no references, exactly as before.

    Raises:
        UnsupportedStageCommandError: never today; reserved for the caller that
            decides to refuse ``LIST`` outright rather than pass it on.
    """
    command_type = detect_command_type(sql)

    stage_refs = []
    for match in _AT_TOKEN.finditer(sql):
        if not _classify_at_token(sql, match):
            continue
        stage_refs.append(
            parse_stage_reference(
                match, sql, name=match.group("name"), path=match.group("path")
            )
        )

    # A ``LIST`` with no stage reference is not a stage command at all — it has
    # nothing to browse. Falling back to REGULAR (rather than keeping
    # STAGE_BROWSE) routes it down the ordinary path, where the engine answers
    # its own syntax error naming ``LIST``. That is not silent: the statement is
    # passed through untouched and fails visibly, which is the honest outcome for
    # a documented command Nova has no implementation for.
    if not stage_refs:
        command_type = CommandType.REGULAR

    return ParsedSQL(
        command_type=command_type,
        stage_refs=stage_refs,
        original_sql=sql,
        base_sql=sql,
    )
