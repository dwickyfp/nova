from __future__ import annotations

from dataclasses import dataclass

_FILE_EXTENSIONS = frozenset(
    {
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
)


@dataclass
class StageReference:
    """Parsed @stage reference from SQL."""

    full_match: str  # Original text: @stage1.data.csv
    stage_name: str  # Stage name: stage1
    path_parts: list[str]  # Remaining path: ['data', 'csv']
    file_name: str | None  # Last part if it looks like a file: 'data.csv'
    is_directory: bool  # True if no file extension
    original_text: str  # The full SQL text for context
    start: int  # Inclusive offset in original_text
    end: int  # Exclusive offset in original_text
    access: str | None = None


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


def stage_reference_from_atom(atom, original_sql: str) -> StageReference:
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

    start = stage_ref.start.start
    end = stage_ref.stop.stop + 1
    return _build_reference(
        full_match=original_sql[start:end],
        stage_name=stage_name,
        raw_parts=raw_parts,
        original_sql=original_sql,
        start=start,
        end=end,
    )


def _build_reference(
    *,
    full_match: str,
    stage_name: str,
    raw_parts: list[str],
    original_sql: str,
    start: int,
    end: int,
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
        start=start,
        end=end,
    )
