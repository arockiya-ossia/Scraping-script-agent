import json

from agent.models.job_record import JobRecord
from agent.tools.validator import check_no_mojibake, check_no_regex, validate_output


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
