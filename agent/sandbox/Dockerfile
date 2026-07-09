FROM python:3.11-slim

# Only what a generated scraper is allowed to use — no LLM client libraries,
# which structurally guarantees "no LLM calls at runtime" (CLAUDE.md §8).
RUN pip install --no-cache-dir \
    requests \
    httpx \
    lxml \
    cssselect \
    jmespath \
    python-dateutil \
    pydantic \
    pycountry

WORKDIR /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
