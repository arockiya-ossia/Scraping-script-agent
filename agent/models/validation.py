from enum import Enum
from typing import Optional

from pydantic import BaseModel


class FailureCategory(str, Enum):
    SYNTAX_ERROR = "syntax_error"
    RUNTIME_ERROR = "runtime_error"
    HTTP_FORBIDDEN = "http_forbidden"
    TIMEOUT = "timeout"
    SCHEMA_DRIFT = "schema_drift"  # API response shape no longer matches what codegen assumed
    PAGINATION_UNDERCOUNT = "pagination_undercount"  # fewer rows than the source's own reported total
    ZERO_RESULTS_FILTER_MISMATCH = "zero_results_filter_mismatch"  # wrong param sent to the source
    ZERO_RESULTS_PARSING_BUG = "zero_results_parsing_bug"  # data came back fine, our parsing logic is wrong
    CONTAINS_REGEX = "contains_regex"  # static check caught `import re` / regex usage
    MOJIBAKE_ENCODING = "mojibake_encoding"  # UTF-8 bytes decoded as Latin-1/cp1252 (Ã©, â€™, etc.)
    OTHER = "other"


class ValidationReport(BaseModel):
    passed: bool
    row_count: int = 0
    non_null_field_rates: dict[str, float] = {}
    all_country_code_is_IN: Optional[bool] = None
    spot_check_urls_ok: Optional[bool] = None
    failure_category: Optional[FailureCategory] = None
    details: str = ""
