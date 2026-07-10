from agent.nodes.discover import _find_ats_link, _is_strong_careers_url, _rank


def test_ignores_unrendered_template_placeholder_hrefs():
    # A client-templating widget that hasn't fully hydrated can leave a
    # literal "{{...}}" placeholder in an href — never a real URL, and
    # dangerously short, so it would otherwise win the "shortest URL" tie
    # break over every real candidate.
    html = (
        '<a href="/jobs/{{cxPropShortenUrl}}">broken</a>'
        '<a href="https://f22labs.zohorecruit.in/jobs/Careers/123/Senior-Project-Manager">real job</a>'
    )
    result = _find_ats_link(html, "https://f22labs.com/careers")
    assert result == "https://f22labs.zohorecruit.in/jobs/Careers/123/Senior-Project-Manager"


def test_returns_none_when_only_template_placeholders_present():
    html = '<a href="/jobs/{{cxPropShortenUrl}}">broken</a>'
    assert _find_ats_link(html, "https://f22labs.com/careers") is None


def test_prefers_shortest_real_ats_link():
    html = (
        '<a href="https://jobs.lever.co/company/abc-123-def">detail</a>'
        '<a href="https://jobs.lever.co/company">listing</a>'
    )
    result = _find_ats_link(html, "https://example.com/careers")
    assert result == "https://jobs.lever.co/company"


def test_ignores_mailto_and_javascript_links():
    html = (
        '<a href="mailto:?body=zohorecruit.in%20job">share</a>'
        '<a href="javascript:void(0)">js</a>'
        '<a href="https://f22labs.zohorecruit.in/jobs/Careers">real</a>'
    )
    result = _find_ats_link(html, "https://f22labs.com/careers")
    assert result == "https://f22labs.zohorecruit.in/jobs/Careers"


def test_careers_subdomain_outranks_marketing_locale_path():
    # The cognizant bug: a careers subdomain (bot-blocks httpx) must outrank a
    # www marketing locale page that merely returns 200, so investigate's
    # Playwright fetch gets a shot at the real ATS.
    careers_sub = "https://careers.cognizant.com/us-en/"
    marketing = "https://www.cognizant.com/au/en/cognizant-careers"
    assert _rank(careers_sub) < _rank(marketing)
    ranked = sorted([marketing, careers_sub], key=_rank)
    assert ranked[0] == careers_sub


def test_is_strong_careers_url_recognizes_subdomain_and_ats():
    assert _is_strong_careers_url("https://careers.example.com/")
    assert _is_strong_careers_url("https://jobs.example.com/all")
    assert _is_strong_careers_url("https://boards.greenhouse.io/example")
    assert not _is_strong_careers_url("https://www.example.com/us-en/careers")
