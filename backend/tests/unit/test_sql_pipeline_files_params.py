"""Unit tests for ``sql_pipeline`` property injection.

The regression these exist for: ``_inject_files_params`` is called twice on the
same statement — once with CSV properties, once with credentials — and an
earlier "already injected?" guard keyed on ``access_key`` alone made the CSV
pass a no-op, because the *translator* had already emitted credentials. A stage
query then returned the file's raw line text as one column instead of parsed
columns.

This is deliberately L1. The defect only showed up at L3 (needing a real
MinIO object and a real engine), which is how it reached CI red; asserting the
injected statement directly catches it without either.
"""

from app.modules.query.dialect.translator import StorageConfig
from app.modules.query.sql_pipeline import _inject_files_params, prepare_stage_sql

#: What the translator emits for a stage reference: credentials already in the
#: FILES() call, no CSV tuning. This shape is what tripped the old guard.
TRANSLATED = (
    "SELECT * FROM FILES('path'='s3://stages/NOVA_ANALYTICS/public/products/products_new.csv', "
    "'format'='csv', 'aws.s3.access_key'='K', 'aws.s3.secret_key'='S')"
)

CSV_PARAMS = {
    "csv.column_separator": ",",
    "csv.trim_space": "true",
    "csv.skip_header": "1",
}
CREDENTIALS = {"aws.s3.access_key": "K", "aws.s3.secret_key": "S"}


class TestCsvParamsSurviveCredentialPresence:
    """The CSV pass must not be suppressed by credentials that already exist."""

    def test_csv_params_injected_despite_existing_credentials(self):
        out = _inject_files_params(TRANSLATED, CSV_PARAMS)
        assert "csv.column_separator" in out
        assert "csv.skip_header" in out
        assert "csv.trim_space" in out
        # And the credentials that were there are still there.
        assert "'aws.s3.access_key'='K'" in out

    def test_csv_params_survive_a_subsequent_credential_pass(self):
        after_csv = _inject_files_params(TRANSLATED, CSV_PARAMS)
        after_creds = _inject_files_params(after_csv, CREDENTIALS)
        assert "csv.column_separator" in after_creds
        assert "csv.skip_header" in after_creds

    def test_both_passes_are_idempotent(self):
        once = _inject_files_params(TRANSLATED, CSV_PARAMS)
        assert _inject_files_params(once, CSV_PARAMS) == once
        twice = _inject_files_params(once, CREDENTIALS)
        assert _inject_files_params(twice, CREDENTIALS) == twice


class TestInjectFilesParamsBasics:
    """The guard is **per key**, not per group or per statement."""

    def test_injects_when_group_absent(self):
        out = _inject_files_params("SELECT * FROM FILES('path'='s3://b/x.csv')", CSV_PARAMS)
        assert "'csv.skip_header'='1'" in out

    def test_no_op_when_group_present(self):
        already = "SELECT * FROM FILES('path'='s3://b/x.csv', 'csv.skip_header'='1')"
        assert _inject_files_params(already, {"csv.skip_header": "1"}) == already

    def test_partial_presence_still_injects_the_missing_keys(self):
        """A key is injected only when *that key* is absent.

        Guards against a half-injected statement from an earlier pass, which
        would otherwise stay half-injected forever, and against appending a
        duplicate of the key that is already there.
        """
        half = "SELECT * FROM FILES('path'='s3://b/x.csv', 'csv.skip_header'='1')"
        out = _inject_files_params(half, CSV_PARAMS)
        assert "csv.column_separator" in out
        assert out.count("csv.skip_header") == 1, out

    def test_no_files_call_is_untouched(self):
        sql = "SELECT 1"
        assert _inject_files_params(sql, CSV_PARAMS) == sql


class TestMultipleFilesCallsInOneStatement:
    """Every ``FILES()`` in the statement is handled, independently.

    A statement can reference two stages. The substitution is global by design,
    so a naive implementation that stopped at the first match — or that used a
    statement-level "already injected?" flag — would leave the second call
    untuned and the query would return unparsed rows for whichever stage it
    skipped.
    """

    TWO_FILES = (
        "SELECT * FROM FILES('path'='s3://b/one.csv', 'format'='csv') AS a "
        "JOIN FILES('path'='s3://b/two.csv', 'format'='csv') AS b ON a.id = b.id"
    )

    def test_both_calls_receive_the_params(self):
        out = _inject_files_params(self.TWO_FILES, CSV_PARAMS)
        assert out.count("csv.column_separator") == 2, out
        assert out.count("csv.skip_header") == 2, out

    def test_each_call_is_handled_on_its_own_terms(self):
        """One call already carrying the params must not suppress the other."""
        mixed = (
            "SELECT * FROM "
            "FILES('path'='s3://b/one.csv', 'csv.skip_header'='1') AS a "
            "JOIN FILES('path'='s3://b/two.csv') AS b ON a.id = b.id"
        )
        out = _inject_files_params(mixed, {"csv.skip_header": "1"})
        # The already-tuned call is left alone; the bare one gains the param.
        assert out.count("csv.skip_header") == 2, out

    def test_two_calls_stay_idempotent(self):
        once = _inject_files_params(self.TWO_FILES, CSV_PARAMS)
        assert _inject_files_params(once, CSV_PARAMS) == once

    def test_credentials_are_not_duplicated_across_two_calls(self):
        with_creds = (
            "SELECT * FROM "
            "FILES('path'='s3://b/one.csv', 'aws.s3.access_key'='K') AS a "
            "JOIN FILES('path'='s3://b/two.csv', 'aws.s3.access_key'='K') AS b ON a.id = b.id"
        )
        out = _inject_files_params(with_creds, CREDENTIALS)
        assert out.count("aws.s3.access_key") == 2, out


class TestPrepareStageSqlOrdering:
    """End to end through the shared pipeline, without an engine."""

    def test_stage_query_carries_both_csv_params_and_credentials(self):
        import asyncio

        stage_configs = {
            "products": StorageConfig(
                storage_type="s3",
                endpoint="http://minio:9000",
                bucket="stages",
                base_prefix="NOVA_ANALYTICS/public/products",
                access_key="K",
                secret_key="S",
                region="us-east-1",
            )
        }

        prepared = asyncio.run(
            prepare_stage_sql(
                "SELECT * FROM @products.products_new.csv LIMIT 5",
                stage_configs=stage_configs,
                csv_params=CSV_PARAMS,
                csv_columns=["id", "name", "amount"],
            )
        )

        assert "@products" not in prepared.engine_sql
        assert "FILES(" in prepared.engine_sql
        assert "csv.column_separator" in prepared.engine_sql
        assert "csv.skip_header" in prepared.engine_sql
        assert prepared.csv_columns == ["id", "name", "amount"]
        # The redacted form must not carry the credential values.
        assert "'K'" not in prepared.redacted_sql
        assert "'***'" in prepared.redacted_sql
