"""Package the reviewed inventory; the importer also checks the installed PG schema."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from app.core.metadata_import_contract import CONTRACT_FORMAT, CONTRACT_PATH, load_contract


def generate(root: Path) -> None:
    inventory = root / "docs/architecture/production-refactor"
    snapshot = json.loads((inventory / "inventory.json").read_text())
    observed = snapshot["live"]["tables"]
    with (inventory / "METADATA_OWNERSHIP.csv").open() as handle:
        owners = list(csv.DictReader(handle))
    with (inventory / "COLUMN_TRANSFORMS.csv").open() as handle:
        transforms = {(r["source_table"], r["source_column"]): r for r in csv.DictReader(handle)}
    tables = []
    for owner in owners:
        name = owner["source_table"]
        source = observed.get(name)
        columns = []
        if source:
            for column in source["columns"]:
                reviewed = transforms[name, column["name"]]
                if (
                    reviewed["source_type"] != column["type"]
                    or reviewed["source_ddl_sha256"] != source["ddl_sha256"]
                ):
                    raise ValueError("Reviewed source schema differs from inventory")
                columns.append(
                    {
                        "name": column["name"],
                        "target": column["name"],
                        "source_type": column["type"],
                        "nullable": column["nullable"] == "YES",
                    }
                )
        model = source["key_model"] if source else owner["source_key_model"]
        tables.append(
            {
                "source_table": name,
                "owner_domain": owner["owner_domain"],
                "key_model": model,
                "source_present": source is not None,
                "source_key": source["key_columns"] if source and model == "PRIMARY" else [],
                "columns": columns,
                "target_table": owner["target_pg_table"].removeprefix("nova_meta."),
                "record_id": model == "DUPLICATE",
                "count_query": owner["validation_query"] if source else None,
            }
        )
    document = {
        "format": CONTRACT_FORMAT,
        "source_commit": snapshot["source_commit"],
        "tables": sorted(tables, key=lambda table: table["source_table"]),
    }
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    load_contract()


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[2])
