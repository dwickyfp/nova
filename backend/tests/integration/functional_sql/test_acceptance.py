import os

import pytest

from .run import argument_parser, run


@pytest.mark.functional_sql
async def test_requested_sql_matrix_through_public_mysql(tmp_path):
    missing = [
        name
        for name in ("NOVA_SQL_TEST_PORT", "NOVA_SQL_TEST_API_URL", "NOVA_SQL_TEST_PASSWORD")
        if not os.getenv(name)
    ]
    assert not missing, f"Explicit functional SQL target required: {', '.join(missing)}"
    arguments = argument_parser().parse_args(
        [
            "--suite",
            os.getenv("NOVA_SQL_TEST_SUITE", "all"),
            "--strict",
            "--report-dir",
            os.getenv("NOVA_SQL_TEST_REPORT_DIR", str(tmp_path)),
            "--workers",
            "1",
        ]
    )
    assert await run(arguments) == 0, (
        f"Functional SQL failure or blocker; inspect {arguments.report_dir}"
    )
