from agent.memory.store import MemoryStore, compute_site_signature, url_structure_shape


def test_signature_is_deterministic():
    sig1 = compute_site_signature("Greenhouse job board API", {"title": "Engineer", "id": 1})
    sig2 = compute_site_signature("Greenhouse job board API", {"id": 1, "title": "Engineer"})  # key order differs
    assert sig1 == sig2


def test_signature_differs_by_shape():
    sig1 = compute_site_signature("Greenhouse job board API", {"title": "Engineer", "id": 1})
    sig2 = compute_site_signature("Greenhouse job board API", {"title": "Engineer", "location": "IN"})
    assert sig1 != sig2


def test_signature_differs_by_ats_hint_not_domain():
    # Same response shape, different ATS platform — must NOT collapse to
    # the same signature (that would conflate two unrelated platforms).
    sig1 = compute_site_signature("Greenhouse job board API", {"title": "Engineer"})
    sig2 = compute_site_signature("Lever postings API", {"title": "Engineer"})
    assert sig1 != sig2


def test_signature_never_includes_a_domain_name():
    # Two unrelated companies on the same platform with the same shape
    # MUST share a signature — that's the whole point (cross-run learning,
    # not per-domain memoization).
    sig_company_a = compute_site_signature("Lever postings API", {"title": "Engineer", "id": 1})
    sig_company_b = compute_site_signature("Lever postings API", {"title": "Engineer", "id": 1})
    assert sig_company_a == sig_company_b


def test_url_structure_shape_ignores_company_name():
    # Two different companies on the same ATS platform must normalize to
    # the same structural shape — passing raw URLs would leak the company
    # name into the signature (every job URL contains it), defeating the
    # entire point of cross-run, platform-level (not domain-level) memory.
    company_a = ["https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4708904005"]
    company_b = ["https://job-boards.greenhouse.io/someothercompany/jobs/9988001"]
    assert url_structure_shape(company_a) == url_structure_shape(company_b)


def test_url_structure_shape_differs_for_different_path_depth():
    flat = ["https://jobs.lever.co/paytm/e5145511-6cbd-4168-a5ad-24bc925487db"]
    nested = ["https://f22labs.zohorecruit.in/jobs/Careers/65449000002129032/Senior-Project-Manager"]
    assert url_structure_shape(flat) != url_structure_shape(nested)


def test_record_and_get_roundtrip(tmp_path):
    store = MemoryStore(path=tmp_path / "patterns.json")
    sig = "abc123"
    store.record(
        sig,
        source_type="rest_api",
        pagination_mechanism="offset/limit",
        pagination_param="offset",
        india_filter_mechanism="query param country=IN",
        ats_hint="Greenhouse job board API",
    )
    entry = store.get(sig)
    assert entry["pagination_mechanism"] == "offset/limit"
    assert entry["pagination_param"] == "offset"
    assert entry["hit_count"] == 1


def test_record_increments_hit_count_on_repeat(tmp_path):
    store = MemoryStore(path=tmp_path / "patterns.json")
    sig = "abc123"
    for _ in range(3):
        store.record(sig, source_type="rest_api", pagination_mechanism="offset/limit", pagination_param="offset", india_filter_mechanism=None, ats_hint=None)
    assert store.get(sig)["hit_count"] == 3


def test_suggest_pagination_param_returns_none_for_unknown_signature(tmp_path):
    store = MemoryStore(path=tmp_path / "patterns.json")
    assert store.suggest_pagination_param("never-seen") is None
    assert store.suggest_india_filter("never-seen") is None


def test_suggest_pagination_param_after_record(tmp_path):
    store = MemoryStore(path=tmp_path / "patterns.json")
    store.record("sig1", source_type="rest_api", pagination_mechanism="page number", pagination_param="page", india_filter_mechanism="query param location=India", ats_hint=None)
    assert store.suggest_pagination_param("sig1") == "page"
    assert store.suggest_india_filter("sig1") == "query param location=India"


def test_persists_across_instances(tmp_path):
    path = tmp_path / "patterns.json"
    store1 = MemoryStore(path=path)
    store1.record("sig1", source_type="rest_api", pagination_mechanism="offset/limit", pagination_param="offset", india_filter_mechanism=None, ats_hint=None)

    store2 = MemoryStore(path=path)  # fresh instance, same file
    assert store2.suggest_pagination_param("sig1") == "offset"


def test_corrupt_store_file_does_not_crash(tmp_path):
    path = tmp_path / "patterns.json"
    path.write_text("not valid json{{{", encoding="utf-8")
    store = MemoryStore(path=path)
    assert store.get("anything") is None
