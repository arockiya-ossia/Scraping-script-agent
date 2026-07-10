import agent.tools.firecrawl_client as fc


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeHTTPXClient:
    """Stands in for FirecrawlClient's internal httpx.Client — returns a
    scripted sequence of remaining_credits for GET /team/credit-usage and a
    fixed scrape response for POST /scrape.
    """

    def __init__(self, credit_sequence, scrape_payload):
        self._credits = iter(credit_sequence)
        self._scrape_payload = scrape_payload

    def get(self, path):
        return FakeResponse({"data": {"remaining_credits": next(self._credits)}})

    def post(self, path, json):
        return FakeResponse(self._scrape_payload)


def _make_client(credit_sequence, scrape_payload):
    client = fc.FirecrawlClient(api_key="dummy-test-key")
    client._client = FakeHTTPXClient(credit_sequence, scrape_payload)
    return client


def test_scrape_tracks_real_credit_delta_not_an_estimate():
    client = _make_client(
        credit_sequence=[1000, 998],  # 2 credits actually consumed
        scrape_payload={"success": True, "data": {"html": "<html></html>", "links": []}},
    )
    result = client.scrape("https://example.com")
    assert result.credits_used == 2
    assert client.total_credits_used == 2


def test_scrape_accumulates_credits_across_multiple_calls():
    client = _make_client(
        credit_sequence=[1000, 999, 999, 997],  # call 1: -1, call 2: -2
        scrape_payload={"success": True, "data": {"html": "<html></html>", "links": []}},
    )
    client.scrape("https://example.com/a")
    client.scrape("https://example.com/b")
    assert client.total_credits_used == 3
    assert client.total_calls == 2


def test_scrape_parses_result_fields():
    client = _make_client(
        credit_sequence=[100, 99],
        scrape_payload={
            "success": True,
            "data": {
                "html": "<html><body>hi</body></html>",
                "markdown": "hi",
                "links": ["https://example.com/jobs/1"],
                "metadata": {"statusCode": 200},
            },
        },
    )
    result = client.scrape("https://example.com")
    assert result.success is True
    assert result.html == "<html><body>hi</body></html>"
    assert result.markdown == "hi"
    assert result.links == ["https://example.com/jobs/1"]
    assert result.status_code == 200


def test_firecrawl_credits_used_is_zero_when_never_invoked():
    fc._client_singleton = None
    assert fc.firecrawl_credits_used() == 0


def test_get_firecrawl_client_is_a_singleton(monkeypatch):
    fc._client_singleton = None
    monkeypatch.setattr("config.settings.firecrawl_api_key", "dummy-test-key")
    first = fc.get_firecrawl_client()
    second = fc.get_firecrawl_client()
    assert first is second
    fc._client_singleton = None
