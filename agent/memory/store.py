"""Cross-run memory of learned platform patterns (CLAUDE.md §12 stretch
goal). Keyed by a *site signature* — an ATS fingerprint plus a hash of the
observed response shape — never by domain. Two unrelated companies running
the same ATS platform with the same response shape share a memory entry;
this is what makes it genuine cross-run learning rather than a hidden
per-domain hardcode (Constraint #2).

Stored hints are surfaced to `investigate.py` as a *prior guess to check
first* — the pagination/filter param that worked last time gets tried
before the generic default list, saving a probe round-trip or two — but
`pagination_param_confirmed` is still only ever set from an actual
`probe_endpoint` diff. A stale or wrong memory hint just means the first
guess misses and the normal empirical trial list runs anyway; it can never
short-circuit confirmation.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from config import settings

DEFAULT_STORE_PATH = Path(__file__).resolve().parent / "learned_patterns.json"


def url_structure_shape(urls: Iterable[str]) -> str:
    """Reduces a set of job-posting URLs to their path *structure* — each
    segment replaced with "N" (numeric) or "S" (slug/string) — so
    "/razorpaysoftwareprivatelimited/jobs/4708904005" and
    "/some-other-company/jobs/9988001" both normalize to "S/S/N" and share
    a signature. Passing the raw URLs themselves would leak the domain into
    the signature through the back door (every company's URL path contains
    its own name), defeating the entire point of keying memory by platform
    shape rather than by domain.
    """
    shapes = set()
    for url in urls:
        segments = [s for s in urlparse(url).path.split("/") if s]
        shapes.add("/".join("N" if s.isdigit() else "S" for s in segments))
    return ",".join(sorted(shapes))


def compute_site_signature(ats_hint: Optional[str], shape_keys: Any) -> str:
    """ats_hint: the horizontal ATS-fingerprint string (e.g. "Greenhouse job
    board API..."), or None. shape_keys: any hashable/iterable description
    of the observed response shape — a sample record's sorted top-level
    keys for a REST/GraphQL source, or the count-bucket of job links found
    for an SSR source. Never a domain name.
    """
    if isinstance(shape_keys, dict):
        keys_part = ",".join(sorted(str(k) for k in shape_keys.keys()))
    elif isinstance(shape_keys, (list, set, frozenset, tuple)):
        keys_part = ",".join(sorted(str(k) for k in shape_keys))
    else:
        keys_part = str(shape_keys)
    raw = f"{ats_hint or 'unknown'}::{keys_part}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class MemoryStore:
    def __init__(self, path: Path = DEFAULT_STORE_PATH):
        self.path = path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get(self, signature: str) -> Optional[dict]:
        return self._data.get(signature)

    def record(
        self,
        signature: str,
        source_type: Optional[str],
        pagination_mechanism: Optional[str],
        pagination_param: Optional[str],
        india_filter_mechanism: Optional[str],
        ats_hint: Optional[str],
    ) -> None:
        entry = self._data.get(signature, {"hit_count": 0})
        entry.update(
            {
                "source_type": source_type,
                "pagination_mechanism": pagination_mechanism,
                "pagination_param": pagination_param,
                "india_filter_mechanism": india_filter_mechanism,
                "ats_hint": ats_hint,
                "hit_count": entry.get("hit_count", 0) + 1,
                "last_seen": time.time(),
            }
        )
        self._data[signature] = entry
        self._save()

    def suggest_pagination_param(self, signature: str) -> Optional[str]:
        entry = self.get(signature)
        return entry.get("pagination_param") if entry else None

    def suggest_india_filter(self, signature: str) -> Optional[str]:
        entry = self.get(signature)
        return entry.get("india_filter_mechanism") if entry else None


memory_store = MemoryStore()
