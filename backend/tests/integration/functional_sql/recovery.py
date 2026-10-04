from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from .contracts import Outcome
from .ml import MLFixtures
from .reporting import write_reports
from .runtime import NovaAPI, Target
from .stages import StageFixtures
from .tasks import TaskFixtures


def validate_journal(journal: dict, target: Target) -> None:
    if not __debug__:
        raise RuntimeError("Fixture ownership verification requires Python assertions enabled")
    assert journal["target"] == f"{target.host}:{target.port}", "Fixture target differs"
    assert journal["api_url"].rstrip("/") == target.api_url.rstrip("/"), "Fixture API differs"
    assert journal.get("user", target.user) == target.user, "Fixture principal differs"
    assert journal.get("role", target.role) == target.role, "Fixture role differs"
    database = journal["database"]
    assert re.fullmatch(r"nova_sql_test_[0-9a-f]{12}", database), "Unowned database"
    for stage in journal.get("stages", []):
        UUID(stage["id"])
        assert re.fullmatch(r"nova_sql_files_[0-9a-f]{12}", stage["name"]), "Unowned stage"
        assert stage["database_name"] == database, "Stage scope differs"
    for model in journal.get("models", []):
        assert re.fullmatch(r"nova_sql_model_[0-9a-f]{12}", model["name"]), "Unowned model"
        assert model["database_name"] == database, "Model scope differs"
    tasks = journal.get("tasks", [])
    if tasks:
        assert len(tasks) == 3, "Incomplete task journal"
        assert re.fullmatch(r"nova_sql_task_[0-9a-f]{12}", tasks[0]), "Unowned task"
        assert tasks[1] == tasks[0] + "_invalid", "Invalid task scope"
        assert re.fullmatch(r"nova_sql_graph_[0-9a-f]{12}", tasks[2]), "Unowned graph"
        assert re.fullmatch(r"nova_sql_native_[0-9a-f]{12}", journal["native_task"])


async def recover(path: Path, target: Target) -> int:
    journal = json.loads(path.read_text())
    outcomes = []
    api = NovaAPI(target)
    directory = path.parent / ("recovery_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        validate_journal(journal, target)
        await api.login()
        database = journal["database"]
        stages = StageFixtures(api, database, "")
        stages.stages = journal.get("stages", [])
        models = MLFixtures(api, database)
        models.models = journal.get("models", [])
        tasks = TaskFixtures(api, database)
        if journal.get("tasks"):
            tasks.selected = True
            tasks.names = journal["tasks"]
            tasks.name, _, tasks.run_name = tasks.names
            tasks.native_name = journal["native_task"]
        for name, fixture, selected in (
            ("tasks", tasks, tasks.selected),
            ("models", models, bool(models.models)),
            ("stages", stages, bool(stages.stages)),
        ):
            if not selected:
                continue
            try:
                await fixture.close()
                outcomes.append(Outcome("cleanup." + name, "cleanup", "PASS"))
            except Exception as exc:
                outcomes.append(
                    Outcome("cleanup." + name, "cleanup", "FAIL", detail=target.failure(exc))
                )
        if not any(item.status != "PASS" for item in outcomes):
            await api.sql(f"DROP DATABASE IF EXISTS `{database}` FORCE", confirm=True)
            observed = await api.sql(f"SHOW DATABASES LIKE '{database}'")
            assert not observed[0]["rows"], "Fixture database remains"
            outcomes.append(Outcome("cleanup.database", "cleanup", "PASS"))
    except Exception as exc:
        outcomes.append(
            Outcome("cleanup.preflight", "cleanup", "BLOCKED", detail=target.failure(exc))
        )
    finally:
        try:
            await api.close()
        except Exception as exc:
            outcomes.append(
                Outcome("cleanup.api_session", "cleanup", "FAIL", detail=target.failure(exc))
            )
    verified = bool(outcomes) and all(item.status == "PASS" for item in outcomes)
    summary = write_reports(directory, outcomes, {}, {"cleanup_verified": verified})
    if verified:
        journal["cleanup_verified"] = True
        journal["database_created"] = False
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(journal, indent=2))
        temporary.chmod(0o600)
        temporary.replace(path)
    print(json.dumps(summary, indent=2))
    print(f"Reports: {directory}")
    return 0 if verified else 1
