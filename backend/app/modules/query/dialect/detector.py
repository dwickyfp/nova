"""Auto-detect file format from S3 object key or content.

Used when a stage reference doesn't have a file extension
(e.g. @stage1.data/ with no specific file).
"""

import os

from app.modules.query.dialect.translator import detect_format_from_filename

# Magic bytes for format detection
_MAGIC_BYTES = {
    b'PAR1': 'parquet',          # Parquet magic
    b'ORC': 'orc',               # ORC magic
    b'\x1f\x8b': 'csv',          # gzip (assume CSV inside)
    b'{': 'json',                # JSON (starts with {)
    b'[': 'json',                # JSON array
}


def detect_format_from_key(object_key: str) -> str:
    """Detect format from S3 object key (filename).

    Args:
        object_key: S3 object key, e.g. "datalake/bronze/stage1/data.csv"

    Returns:
        Format string: csv, parquet, json, orc, etc.
    """
    filename = os.path.basename(object_key)
    return detect_format_from_filename(filename)


def detect_format_from_content(header_bytes: bytes) -> str:
    """Detect format from the first few bytes of a file.

    Args:
        header_bytes: First 4+ bytes of the file

    Returns:
        Format string, or 'csv' if unknown.
    """
    for magic, fmt in _MAGIC_BYTES.items():
        if header_bytes.startswith(magic):
            return fmt

    # Try text-based detection
    try:
        text = header_bytes.decode('utf-8', errors='ignore')
        if text.startswith('{') or text.startswith('['):
            return 'json'
        # If it looks like delimited text
        if ',' in text or '\t' in text:
            return 'csv'
    except Exception:
        pass

    return 'csv'  # Default fallback


def detect_format_from_listing(
    object_keys: list[str],
    sample_size: int = 10,
) -> str:
    """Detect the dominant format from a list of S3 object keys.

    Args:
        object_keys: List of S3 object keys
        sample_size: Number of files to sample

    Returns:
        Most common format among the sampled files.
    """
    if not object_keys:
        return 'csv'

    from collections import Counter

    formats = Counter()
    for key in object_keys[:sample_size]:
        fmt = detect_format_from_key(key)
        formats[fmt] += 1

    return formats.most_common(1)[0][0] if formats else 'csv'
