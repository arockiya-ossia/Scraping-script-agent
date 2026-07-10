import json

from agent.models.job_record import JobRecord
from agent.tools.validator import check_no_mojibake, check_no_regex, validate_output, write_confidence_sidecar


def test_requests_import_is_not_a_false_positive():
    # "import re" is a literal substring of "import requests" — a naive
    # substring check would wrongly reject this.
    source = "import requests\n\ndef main():\n    requests.get('https://example.com')\n"
    assert check_no_regex(source) is True


def test_actual_re_import_is_caught():
    assert check_no_regex("import re\n") is False


def test_re_import_from_is_caught():
    assert check_no_regex("from re import compile\n") is False


def test_re_call_usage_is_caught():
    source = "import re as regex_module\n\ndef f(s):\n    return regex_module.match('x', s)\n"
    assert check_no_regex(source) is False


def test_readline_and_resource_imports_are_not_false_positives():
    assert check_no_regex("import readline\n") is True
    assert check_no_regex("import resource\n") is True


def test_syntax_error_source_does_not_crash_the_check():
    assert check_no_regex("def broken(:\n") is True


def test_clean_text_has_no_mojibake():
    record = JobRecord(title="India's growth story", job_description="Quotes: \"fine\" and it's fine.")
    assert check_no_mojibake([record]) is True


def test_correctly_decoded_unicode_is_not_flagged():
    # A properly UTF-8-decoded accented letter must not be mistaken for the
    # mis-decoded "Ã©" pattern it would produce if decoding went wrong.
    record = JobRecord(title="Café Coordinator", city="Montreal")
    assert check_no_mojibake([record]) is True


def test_right_single_quote_mojibake_is_detected():
    # UTF-8 bytes for U+2019 (') misread as cp1252: U+00E2 U+20AC U+2122.
    mojibake_apostrophe = "â€™"
    record = JobRecord(job_description=f"India{mojibake_apostrophe}s leading fintech")
    assert check_no_mojibake([record]) is False


def test_replacement_character_is_detected():
    record = JobRecord(title="Engineer� Role")
    assert check_no_mojibake([record]) is False


def test_validate_output_flags_mojibake_as_failure_category(tmp_path):
    mojibake_apostrophe = "â€™"
    record = {
        "title": "Engineer",
        "job_id": "1",
        "url": "https://example.com/1",
        "country_code": "IN",
        "job_description": f"India{mojibake_apostrophe}s growth",
    }
    output_path = tmp_path / "output.jsonl"
    output_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    report = validate_output(script_source="import requests\n", output_path=str(output_path), spot_check=False)
    assert report.passed is False
    assert report.failure_category.value == "mojibake_encoding"


def test_write_confidence_sidecar_keys_by_job_id(tmp_path):
    records = [
        JobRecord(job_id="1", title="Engineer", city="Bengaluru"),
        JobRecord(job_id="2", title="Manager", city=None),
    ]
    non_null_rates = {"title": 1.0, "city": 0.5, "job_id": 1.0}
    output_path = tmp_path / "output.jsonl"
    path = write_confidence_sidecar(records, non_null_rates, str(output_path))
    assert path == str(tmp_path / "confidence.json")
    sidecar = json.loads((tmp_path / "confidence.json").read_text(encoding="utf-8"))
    assert set(sidecar.keys()) == {"1", "2"}


def test_write_confidence_sidecar_present_field_gets_dataset_rate(tmp_path):
    records = [JobRecord(job_id="1", title="Engineer")]
    non_null_rates = {"title": 0.9}
    output_path = tmp_path / "output.jsonl"
    write_confidence_sidecar(records, non_null_rates, str(output_path))
    sidecar = json.loads((tmp_path / "confidence.json").read_text(encoding="utf-8"))
    assert sidecar["1"]["title"] == 0.9


def test_write_confidence_sidecar_missing_field_is_zero(tmp_path):
    records = [JobRecord(job_id="1", title=None)]
    non_null_rates = {"title": 0.7}  # dataset-wide rate is high...
    output_path = tmp_path / "output.jsonl"
    write_confidence_sidecar(records, non_null_rates, str(output_path))
    sidecar = json.loads((tmp_path / "confidence.json").read_text(encoding="utf-8"))
    # ...but THIS job's title is missing, so its confidence is 0, not 0.7.
    assert sidecar["1"]["title"] == 0.0


def test_write_confidence_sidecar_falls_back_to_row_index_without_job_id(tmp_path):
    records = [JobRecord(job_id=None, title="Engineer")]
    output_path = tmp_path / "output.jsonl"
    write_confidence_sidecar(records, {"title": 1.0}, str(output_path))
    sidecar = json.loads((tmp_path / "confidence.json").read_text(encoding="utf-8"))
    assert "_row_0" in sidecar


def test_validate_output_writes_confidence_sidecar_even_on_later_failure(tmp_path):
    # A run that fails a LATER check (non-IN country code) should still
    # leave the per-field confidence data behind — it's diagnostic
    # information independent of the overall pass/fail verdict.
    records = [{"title": "Engineer", "job_id": "1", "url": "https://example.com/1", "country_code": "US"}]
    output_path = tmp_path / "output.jsonl"
    output_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    report = validate_output(script_source="import requests\n", output_path=str(output_path), spot_check=False)
    assert report.passed is False
    assert (tmp_path / "confidence.json").exists()
