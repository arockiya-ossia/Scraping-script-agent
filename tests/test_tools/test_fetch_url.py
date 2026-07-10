import time

from playwright.sync_api import sync_playwright

from agent.tools.fetch_url import _Budget, _run_interactions

HTML_FIXTURE = """
<html><body>
<button id="reveal">Search Jobs</button>
<script>
document.getElementById('reveal').addEventListener('click', function () {
    fetch('/api/jobs').then(function (r) { return r.json(); });
});
</script>
</body></html>
"""


def test_click_reveals_jobs_via_xhr():
    """End-to-end (browser-only, no live site): a generic "Search Jobs"
    button click fires an XHR that our network capture would pick up in
    fetch_url — proves the click-detection -> click -> capture pipeline
    genuinely works, independent of any real website's quirks (bot
    protection, layout drift, availability).
    """
    captured = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def handle_page(route):
            route.fulfill(status=200, content_type="text/html", body=HTML_FIXTURE)

        def handle_api(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"jobs": [{"id": 1, "title": "Engineer"}]}',
            )

        # set_content() leaves the page at about:blank, which has no origin
        # for a relative fetch('/api/jobs') to resolve against — route both
        # the document and the API off a real (intercepted, not live) origin.
        page.route("https://example.test/", handle_page)
        page.route("https://example.test/api/jobs", handle_api)

        def on_response(response):
            req = response.request
            if req.resource_type in ("xhr", "fetch"):
                captured.append({"url": response.url, "status": response.status, "body": response.text()})

        page.on("response", on_response)
        page.goto("https://example.test/")

        budget = _Budget(max_interactions=10, max_scrolls=5, max_page_time=30.0, started=time.monotonic())
        log = _run_interactions(page, budget)

        browser.close()

    assert any(action["action"] == "click" and action["target"] == "search jobs" for action in log)
    assert any(c["body"] and "Engineer" in c["body"] for c in captured)


def test_budget_stops_after_max_interactions():
    budget = _Budget(max_interactions=0, max_scrolls=0, max_page_time=30.0, started=time.monotonic())
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(HTML_FIXTURE)
        log = _run_interactions(page, budget)
        browser.close()
    assert log == []
