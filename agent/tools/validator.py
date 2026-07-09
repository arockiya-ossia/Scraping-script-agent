"""Deterministic checks run against generated scraper source and its JSONL
output (CLAUDE.md §11). Cheapest checks first so a bad script fails fast.
"""

import json

import httpx
from pydantic import ValidationError

from agent.models.job_record import JobRecord
from agent.models.validation import FailureCategory, ValidationReport

REGEX_MARKERS = ("import re", "re.compile", "re.match", "re.search", "re.findall", "re.sub")


def check_no_regex(source: str) -> bool:
    """Returns True if the source is clean (no regex usage found)."""
    return not any(marker in source for marker in REGEX_MARKERS)


def validate_output(
    script_source: str,
    output_path: str,
    reported_total_count: int | None = None,
    spot_check: bool = True,
) -> ValidationReport:
    if not check_no_regex(script_source):
        return ValidationReport(
            passed=False,
            failure_category=FailureCategory.CONTAINS_REGEX,
            details="Static check found `import re` or regex usage in generated source.",
        )

    try:
        with open(output_path, encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if line.strip()]
    except FileNotFoundError:
        return ValidationReport(
            passed=False,
            failure_category=FailureCategory.RUNTIME_ERROR,
            details=f"Output file not found: {output_path}",
        )

    records: list[JobRecord] = []
    for i, line in enumerate(lines):
        try:
            records.append(JobRecord.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            return ValidationReport(
                passed=False,
                failure_category=FailureCategory.SCHEMA_DRIFT,
                details=f"Line {i} does not parse as JobRecord: {exc}",
            )

    row_count = len(records)
    if row_count == 0:
        return ValidationReport(
            passed=False,
            row_count=0,
            failure_category=FailureCategory.ZERO_RESULTS_PARSING_BUG,
            details="Output file is empty.",
        )

    if reported_total_count is not None and row_count < reported_total_count * 0.8:
        return ValidationReport(
            passed=False,
            row_count=row_count,
            failure_category=FailureCategory.PAGINATION_UNDERCOUNT,
            details=f"Got {row_count} rows, source reported {reported_total_count}.",
        )

    non_null_rates: dict[str, float] = {}
    for field in JobRecord.model_fields:
        non_null = sum(1 for r in records if getattr(r, field) is not None)
        non_null_rates[field] = non_null / row_count

    for required in ("title", "job_id", "url"):
        if non_null_rates[required] < 0.5:
            return ValidationReport(
                passed=False,
                row_count=row_count,
                non_null_field_rates=non_null_rates,
                failure_category=FailureCategory.SCHEMA_DRIFT,
                details=f"Required field '{required}' is null in >50% of rows.",
            )

    country_codes = {r.country_code for r in records if r.country_code is not None}
    all_india = country_codes.issubset({"IN"})
    if not all_india:
        return ValidationReport(
            passed=False,
            row_count=row_count,
            non_null_field_rates=non_null_rates,
            all_country_code_is_IN=False,
            failure_category=FailureCategory.ZERO_RESULTS_FILTER_MISMATCH,
            details=f"Found non-IN country codes: {country_codes - {'IN'}}",
        )

    spot_check_ok: bool | None = None
    if spot_check:
        sample = [r for r in records[:3] if r.url]
        spot_check_ok = True
        for r in sample:
            try:
                resp = httpx.head(r.url, follow_redirects=True, timeout=10.0)
                if resp.status_code >= 400:
                    resp = httpx.get(r.url, follow_redirects=True, timeout=10.0)
                if resp.status_code >= 400:
                    spot_check_ok = False
                    break
            except httpx.HTTPError:
                spot_check_ok = False
                break

    return ValidationReport(
        passed=True,
        row_count=row_count,
        non_null_field_rates=non_null_rates,
        all_country_code_is_IN=all_india,
        spot_check_urls_ok=spot_check_ok,
        details="All checks passed.",
    )
