from enum import Enum
from typing import Optional

from pydantic import BaseModel


class SourceType(str, Enum):
    SSR_HTML = "ssr_html"  # job links present in plain (no-JS) HTML — a requests scraper works
    EMBEDDED_JSON = "embedded_json"
    REST_API = "rest_api"
    GRAPHQL = "graphql"
    SPA_RENDERED = "spa_rendered"  # job links ONLY appear after JS renders, but the rendered
    # DOM is stable and enumerable — a standalone Playwright scraper is a valid source (it runs
    # with no LLM calls, just a browser). Distinct from SPA_NO_API, which is a genuine dead end.
    SPA_NO_API = "spa_no_api"  # JS-only page with no discoverable API AND no enumerable rendered links
    UNKNOWN = "unknown"


class PaginationStatus(str, Enum):
    """Pagination is not a boolean — "not confirmed" conflates "we proved it
    needs no pagination" with "we couldn't figure it out", which are very
    different sufficiency signals. A stable, complete single-page listing is
    a perfectly scrapable source; an unknown pagination scheme is not.
    """

    CONFIRMED = "confirmed"  # a pagination mechanism was empirically observed to yield new records
    NOT_REQUIRED = "not_required"  # stable complete listing, no next/load-more/cursor, no unseen records
    UNKNOWN = "unknown"  # a pagination control exists but its mechanism couldn't be established
    FAILED = "failed"  # probing errored out / the source didn't respond


class InvestigationEvidence(BaseModel):
    careers_url: Optional[str] = None
    source_type: Optional[SourceType] = None
    pagination_status: Optional[PaginationStatus] = None
    pagination_mechanism: Optional[str] = None  # e.g. "offset/limit", "cursor", "page number", "load more"
    india_filter_mechanism: Optional[str] = None  # e.g. "query param country=IN", or "client_side_fallback"
    requires_browser: bool = False  # the generated scraper must render JS (Playwright), not just fetch HTML
    requires_firecrawl: bool = False  # site edge-blocks plain fetch AND Playwright — only Firecrawl's
    # stealth rendering reached it during investigation, so the generated scraper must fetch/render via
    # the Firecrawl API at runtime (Firecrawl is a fetch/render service, not an LLM — the "no LLM calls at
    # runtime" rule is preserved). Set only when Playwright empirically failed and Firecrawl succeeded.
    reported_total_count: Optional[int] = None  # if the source itself reports a total
    evidence_notes: str = ""  # LLM's reasoning, goes straight into the trace

    @property
    def pagination_param_confirmed(self) -> bool:
        """Back-compat read for trace/fingerprint code. A source is "good to
        paginate" when the mechanism is confirmed OR provably not needed.
        """
        return self.pagination_status in (PaginationStatus.CONFIRMED, PaginationStatus.NOT_REQUIRED)

    def is_sufficient(self) -> bool:
        return (
            self.careers_url is not None
            and self.source_type is not None
            and self.pagination_status in (PaginationStatus.CONFIRMED, PaginationStatus.NOT_REQUIRED)
            and self.india_filter_mechanism is not None
        )
