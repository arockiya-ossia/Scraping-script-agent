from agent.models.evidence import InvestigationEvidence, PaginationStatus, SourceType


def test_insufficient_when_empty():
    evidence = InvestigationEvidence()
    assert evidence.is_sufficient() is False


def test_insufficient_when_pagination_unknown():
    evidence = InvestigationEvidence(
        careers_url="https://example.com/careers",
        source_type=SourceType.REST_API,
        pagination_status=PaginationStatus.UNKNOWN,
        india_filter_mechanism="query param country=IN",
    )
    assert evidence.is_sufficient() is False


def test_sufficient_when_pagination_confirmed():
    evidence = InvestigationEvidence(
        careers_url="https://example.com/careers",
        source_type=SourceType.REST_API,
        pagination_status=PaginationStatus.CONFIRMED,
        india_filter_mechanism="query param country=IN",
    )
    assert evidence.is_sufficient() is True


def test_sufficient_when_pagination_not_required():
    # A stable, complete single-page listing needs no pagination — that's a
    # perfectly scrapable source, NOT the same as an unknown scheme.
    evidence = InvestigationEvidence(
        careers_url="https://example.com/careers",
        source_type=SourceType.SPA_RENDERED,
        pagination_status=PaginationStatus.NOT_REQUIRED,
        india_filter_mechanism="client_side_fallback",
    )
    assert evidence.is_sufficient() is True


def test_pagination_param_confirmed_property_reflects_status():
    e = InvestigationEvidence(pagination_status=PaginationStatus.NOT_REQUIRED)
    assert e.pagination_param_confirmed is True
    e = InvestigationEvidence(pagination_status=PaginationStatus.CONFIRMED)
    assert e.pagination_param_confirmed is True
    e = InvestigationEvidence(pagination_status=PaginationStatus.UNKNOWN)
    assert e.pagination_param_confirmed is False
    e = InvestigationEvidence(pagination_status=None)
    assert e.pagination_param_confirmed is False
