"""Generate the browser's Pydantic/Zod schema fixture without editing application data."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.smart_reports.content import ReportBlock  # noqa: E402
from app.modules.smart_reports.schemas import (  # noqa: E402
    ReportContextRef,
    ReportInteractionEvent,
    ReportPatch,
    ReportSpecV1,
    ReportViewStateV1,
    WidgetResult,
)


def main() -> None:
    target = (
        Path(__file__).resolve().parents[2] / "frontend/src/features/smart-reports/contracts.json"
    )
    target.write_text(
        json.dumps(
            {
                model.__name__: model.model_json_schema()
                for model in (
                    ReportSpecV1,
                    ReportViewStateV1,
                    ReportInteractionEvent,
                    ReportPatch,
                    WidgetResult,
                    ReportContextRef,
                    ReportBlock,
                )
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
