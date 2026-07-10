FROM python:3.11-slim

# Generated scrapers for JS-rendered (SPA) sources need a real browser at
# runtime; API/SSR scrapers ignore it. Still NO LLM client libraries inside
# — the "no LLM calls at runtime" guarantee (CLAUDE.md §8) holds structurally.
RUN pip install --no-cache-dir \
    requests \
    httpx \
    lxml \
    cssselect \
    jmespath \
    python-dateutil \
    pydantic \
    pycountry \
    pypdf \
    playwright

# Install Chromium to a FIXED, world-readable path (not $HOME/.cache) so the
# browser is found at runtime regardless of what HOME the sandbox sets for
# writable scratch dirs. Version-agnostic: pulls whatever browser build
# matches the playwright version pinned above.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium && chmod -R a+rX /ms-playwright

WORKDIR /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
