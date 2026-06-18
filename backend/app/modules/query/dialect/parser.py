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


# Pattern: @stage_name[.path.parts...]
# Captures: @word.word.word...
_STAGE_PATTERN = re.compile(
    r'@([a-zA-Z_][a-zA-Z0-9_]*)'  # stage name
    r'((?:\.[a-zA-Z0-9_]+)*)'       # optional .path.parts
    r'(/)?'                          # optional trailing slash (directory)
    r'(?:\s|$|;|,|\)|\()'           # boundary
)

# File extension pattern
_FILE_EXTENSIONS = {
    'csv', 'tsv', 'json', 'parquet', 'orc', 'avro',
    'txt', 'gz', 'bz2', 'snappy', 'zstd', 'lzo',
}


def parse_stage_reference(match: re.Match, original_sql: str) -> StageReference:
    """Parse a single @stage regex match into a StageReference."""
    stage_name = match.group(1)
    path_str = match.group(2)  # e.g. ".data.folder.file.csv"

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

    Returns ParsedSQL with command type, stage references, and base SQL.
    """
    command_type = detect_command_type(sql)

    # Find all @stage references
    stage_refs = []
    for match in _STAGE_PATTERN.finditer(sql):
        ref = parse_stage_reference(match, sql)
        stage_refs.append(ref)

    # If no @stage found, it's regular SQL
    if not stage_refs:
        command_type = CommandType.REGULAR

    return ParsedSQL(
        command_type=command_type,
        stage_refs=stage_refs,
        original_sql=sql,
        base_sql=sql,
    )
