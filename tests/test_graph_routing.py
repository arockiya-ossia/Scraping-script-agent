from agent.graph import _route_evidence, _route_repair, _route_validation
from agent.models.evidence import InvestigationEvidence, SourceType
from agent.models.validation import FailureCategory, ValidationReport
from agent.nodes.evidence_check import evidence_check


def _state(**overrides):
    base = {
        "domain": "example.com",
        "evidence": InvestigationEvidence(),
        "script_code": None,
        "script_path": None,
        "validation_report": None,
        "repair_attempt": 0,
        "total_attempts": 8,
        "max_total_attempts": 8,
        "status": "running",
        "trace_summary": [],
    }
    base.update(overrides)
    return base


def test_evidence_check_node_decrements_budget_when_insufficient():
    state = _state(evidence=InvestigationEvidence())
    state = evidence_check(state)
    assert state["total_attempts"] == 7
    assert _route_evidence(state) == "insufficient"


def test_route_evidence_sufficient_does_not_touch_budget():
    evidence = InvestigationEvidence(
        careers_url="https://example.com/careers",
        source_type=SourceType.REST_API,
        pagination_param_confirmed=True,
        india_filter_mechanism="query param country=IN",
    )
    state = _state(evidence=evidence, total_attempts=8)
    state = evidence_check(state)
    assert state["total_attempts"] == 8
    assert _route_evidence(state) == "sufficient"


def test_route_evidence_budget_exhausted_routes_to_finalize():
    state = _state(evidence=InvestigationEvidence(), total_attempts=0)
    state = evidence_check(state)
    assert _route_evidence(state) == "budget_exhausted"


def test_route_validation_pass():
    state = _state(validation_report=ValidationReport(passed=True, row_count=5))
    assert _route_validation(state) == "pass"


def test_route_validation_fail_within_budget():
    state = _state(
        validation_report=ValidationReport(passed=False, failure_category=FailureCategory.SYNTAX_ERROR),
        total_attempts=3,
    )
    assert _route_validation(state) == "fail"


def test_route_validation_fail_budget_exhausted():
    state = _state(
        validation_report=ValidationReport(passed=False, failure_category=FailureCategory.SYNTAX_ERROR),
        total_attempts=0,
    )
    assert _route_validation(state) == "budget_exhausted"


def test_route_repair_maps_categories_per_spec_table():
    cases = {
        FailureCategory.SYNTAX_ERROR: "docker_execute",
        FailureCategory.PAGINATION_UNDERCOUNT: "docker_execute",
        FailureCategory.HTTP_FORBIDDEN: "investigate",
        FailureCategory.SCHEMA_DRIFT: "investigate",
        FailureCategory.ZERO_RESULTS_FILTER_MISMATCH: "investigate",
        FailureCategory.ZERO_RESULTS_PARSING_BUG: "docker_execute",
        FailureCategory.CONTAINS_REGEX: "docker_execute",
        FailureCategory.OTHER: "generate_script",
    }
    for category, expected_node in cases.items():
        state = _state(validation_report=ValidationReport(passed=False, failure_category=category))
        assert _route_repair(state) == expected_node, category


def test_route_repair_defaults_to_rewrite_when_category_missing():
    state = _state(validation_report=ValidationReport(passed=False, failure_category=None))
    assert _route_repair(state) == "generate_script"
