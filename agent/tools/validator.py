"""Deterministic checks run against generated scraper source and its JSONL
output (CLAUDE.md §11). Cheapest checks first so a bad script fails fast.
"""

import ast
import json
from pathlib import Path

import httpx
from pydantic import ValidationError

from agent.models.job_record import JobRecord
from agent.models.validation import FailureCategory, ValidationReport


def check_no_regex(source: str) -> bool:
    """Returns True if the source is clean (no `re` module usage found).

    AST-based, not substring-based — a naive substring check on "import re"
    false-positives on "import requests" (it's a literal substring: "import
    re" + "quests"), which would reject every scraper using the one HTTP
    library the sandbox actually ships.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True  # syntax errors are validate.py's job, not this check's

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "re" for alias in node.names):
                return False
        elif isinstance(node, ast.ImportFrom):
            if node.module == "re":
                return False
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "re":
                return False
            if (
                isinstance(func, ast.Name)
                and func.id == "__import__"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "re"
            ):
                return False
    return True


# Byte sequences that appear when UTF-8 text (curly quotes, dashes,
# accented letters) gets decoded as Latin-1/cp1252 somewhere in the
# scraper's HTTP/parsing pipeline — e.g. `requests` not being told the
# response is UTF-8, or manually decoding bytes with the wrong codec.
# Written as \uXXXX escapes, not literal characters, so the marker values
# can't be silently corrupted by a terminal/file encoding round-trip —
# exactly the class of bug this check exists to catch in generated code.
MOJIBAKE_MARKERS = (
    "â€™",  # UTF-8 for U+2019 (') misread as cp1252
    "â€œ",  # UTF-8 for U+201C (") misread as cp1252
    "â€“",  # UTF-8 for U+2013 (en dash) misread as cp1252
    "â€”",  # UTF-8 for U+2014 (em dash) misread as cp1252
    "Ã©",  # UTF-8 for U+00E9 (e-acute) misread as cp1252
    "Ã¨",  # UTF-8 for U+00E8 (e-grave) misread as cp1252
    "Ã±",  # UTF-8 for U+00F1 (n-tilde) misread as cp1252
    "Â ",  # UTF-8 for U+00A0 (non-breaking space) misread as cp1252
    "�",  # replacement character — an outright decode failure
)


def check_no_mojibake(records: list[JobRecord]) -> bool:
    """Returns True if none of the text fields show UTF-8-decoded-as-Latin-1
    mojibake artifacts. Deterministic substring scan — genuinely reliable
    here because these byte sequences essentially never occur in correctly
    decoded English/most-language text, unlike the `import re` false
    positive risk that AST-based check_no_regex had to guard against.
    """
    for record in records:
        for value in record.model_dump().values():
            if isinstance(value, str) and any(marker in value for marker in MOJIBAKE_MARKERS):
                return False
    return True


def write_confidence_sidecar(records: list[JobRecord], non_null_rates: dict[str, float], output_path: str) -> str:
    """Per-field confidence scoring (CLAUDE.md §12 stretch goal): a sidecar
    file, not an extension of the fixed JobRecord schema.

    Computed heuristically from the same non_null_rates the validator
    already builds: a field's non-null rate *across the whole dataset* is
    a proxy for how reliably its selector/path works — a selector that
    matched consistently for every sampled job scores high; one that only
    occasionally produced a value (a fallback/best-effort path, or a field
    genuinely absent for most postings) scores lower. Per job, a field's
    confidence is that dataset-wide rate when the value is actually
    present for that job, or 0.0 when it's missing (nothing to be
    confident about).
    """
    sidecar: dict[str, dict[str, float]] = {}
    for i, record in enumerate(records):
        key = record.job_id or f"_row_{i}"
        dumped = record.model_dump()
        sidecar[key] = {
            field: (non_null_rates.get(field, 0.0) if value is not None else 0.0)
            for field, value in dumped.items()
        }
    confidence_path = Path(output_path).with_name("confidence.json")
    confidence_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return str(confidence_path)


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

    # Written here — before any of the checks below that can still fail —
    # so a run that fails a *later* check (country_code purity, mojibake)
    # still leaves useful per-field diagnostic data behind, not just a
    # failure category.
    write_confidence_sidecar(records, non_null_rates, output_path)

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

    if not check_no_mojibake(records):
        return ValidationReport(
            passed=False,
            row_count=row_count,
            non_null_field_rates=non_null_rates,
            all_country_code_is_IN=all_india,
            failure_category=FailureCategory.MOJIBAKE_ENCODING,
            details="Text fields contain UTF-8-decoded-as-Latin-1 artifacts (e.g. ’/“ misread as cp1252) — the scraper likely isn't decoding HTTP responses as UTF-8.",
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
