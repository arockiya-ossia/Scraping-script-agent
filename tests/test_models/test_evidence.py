from agent.models.evidence import InvestigationEvidence, SourceType


def test_insufficient_when_empty():
    evidence = InvestigationEvidence()
    assert evidence.is_sufficient() is False


def test_insufficient_when_pagination_not_confirmed():
    evidence = InvestigationEvidence(
        careers_url="https://example.com/careers",
        source_type=SourceType.REST_API,
        pagination_param_confirmed=False,
        india_filter_mechanism="query param country=IN",
    )
    assert evidence.is_sufficient() is False


def test_sufficient_when_all_present():
    evidence = InvestigationEvidence(
        careers_url="https://example.com/careers",
        source_type=SourceType.REST_API,
        pagination_param_confirmed=True,
        india_filter_mechanism="query param country=IN",
    )
    assert evidence.is_sufficient() is True
