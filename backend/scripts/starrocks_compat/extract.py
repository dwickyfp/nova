"""Extract every SQL example from the StarRocks 4.1.4 English docs into a JSONL corpus."""

import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])  # .../repo/docs/en
out = Path(sys.argv[2])
fence = re.compile(r"^(```+|~~~+)\s*([A-Za-z0-9_+-]*)[^\n]*$")
heading = re.compile(r"^(#{1,6})\s+(.*)$")
sqlish = re.compile(
    r"^\s*(SELECT|WITH|INSERT|CREATE|ALTER|DROP|SHOW|DESC|DESCRIBE|EXPLAIN|SET|USE|LOAD|EXPORT|SUBMIT|CANCEL|PAUSE|RESUME|STOP|GRANT|REVOKE|UPDATE|DELETE|TRUNCATE|REFRESH|ANALYZE|ADMIN|BACKUP|RESTORE|RECOVER|INSTALL|UNINSTALL|KILL|BEGIN|COMMIT|ROLLBACK|ADD|EXECUTE|PREPARE|CACHE|CLEAR|SYNC|REPAIR|CHECK|MERGE|ALTER)\b",
    re.I,
)
rows = []
for path in sorted(root.rglob("*.md*")):
    rel = str(path.relative_to(root))
    lines = path.read_text(errors="replace").splitlines()
    section, block, lang, start = "", None, "", 0
    for number, line in enumerate(lines, 1):
        if block is None:
            match = heading.match(line)
            if match:
                section = match.group(2).strip()
            match = fence.match(line)
            if match:
                block, lang, start = [], match.group(2).lower(), number
            continue
        if fence.match(line) and not fence.match(line).group(2):
            text = "\n".join(block).strip()
            bare = re.sub(r"^\s*(mysql|starrocks)\s*(\[[^\]]*\])?\s*>\s*", "", text, flags=re.I)
            kind = (
                "sql"
                if lang in {"sql", "mysql", "plsql"}
                else (
                    "maybe"
                    if lang
                    not in {
                        "python",
                        "java",
                        "scala",
                        "json",
                        "yaml",
                        "yml",
                        "xml",
                        "go",
                        "javascript",
                        "js",
                        "properties",
                        "ini",
                        "toml",
                        "dockerfile",
                        "c",
                        "cpp",
                        "rust",
                        "groovy",
                        "kotlin",
                        "csharp",
                        "php",
                        "ruby",
                        "typescript",
                    }
                    and sqlish.match(bare)
                    else None
                )
            )
            if kind and text:
                rows.append(
                    {
                        "page": rel,
                        "section": section,
                        "line": start,
                        "lang": lang,
                        "kind": kind,
                        "text": text,
                    }
                )
            block = None
            continue
        block.append(line)
out.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
by = {}
for row in rows:
    top = row["page"].split("/")[0]
    by[top] = by.get(top, 0) + 1
print(len(rows), "blocks", json.dumps(dict(sorted(by.items(), key=lambda x: -x[1]))))
