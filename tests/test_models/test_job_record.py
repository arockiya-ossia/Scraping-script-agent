from agent.models.job_record import JobRecord


def test_all_fields_default_to_none():
    record = JobRecord()
    for field in JobRecord.model_fields:
        assert getattr(record, field) is None


def test_accepts_partial_data():
    record = JobRecord(title="Software Engineer", country_code="IN")
    assert record.title == "Software Engineer"
    assert record.country_code == "IN"
    assert record.job_id is None
