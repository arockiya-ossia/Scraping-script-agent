from agent.models.validation import FailureCategory, ValidationReport


def test_passed_report_needs_no_failure_category():
    report = ValidationReport(passed=True, row_count=10)
    assert report.failure_category is None


def test_failed_report_carries_category():
    report = ValidationReport(
        passed=False,
        row_count=0,
        failure_category=FailureCategory.ZERO_RESULTS_FILTER_MISMATCH,
        details="server returned 0 rows for country=IN",
    )
    assert report.passed is False
    assert report.failure_category == FailureCategory.ZERO_RESULTS_FILTER_MISMATCH
