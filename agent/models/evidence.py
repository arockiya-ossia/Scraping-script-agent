from enum import Enum
from typing import Optional

from pydantic import BaseModel


class SourceType(str, Enum):
    SSR_HTML = "ssr_html"
    EMBEDDED_JSON = "embedded_json"
    REST_API = "rest_api"
    GRAPHQL = "graphql"
    SPA_NO_API = "spa_no_api"
    UNKNOWN = "unknown"


class InvestigationEvidence(BaseModel):
    careers_url: Optional[str] = None
    source_type: Optional[SourceType] = None
    pagination_param_confirmed: bool = False  # actually tested via probe_endpoint, not guessed
    pagination_mechanism: Optional[str] = None  # e.g. "offset/limit", "cursor", "page number", "infinite scroll"
    india_filter_mechanism: Optional[str] = None  # e.g. "query param country=IN", or "client_side_fallback"
    reported_total_count: Optional[int] = None  # if the source itself reports a total
    evidence_notes: str = ""  # LLM's reasoning, goes straight into the trace

    def is_sufficient(self) -> bool:
        return (
            self.careers_url is not None
            and self.source_type is not None
            and self.pagination_param_confirmed
            and self.india_filter_mechanism is not None
        )
