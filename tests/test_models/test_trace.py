from agent.models.trace import TraceEvent


def test_timestamp_defaults_and_varies_per_instance():
    event_a = TraceEvent(domain="example.com", node="discover", event_type="node_enter", summary="start")
    event_b = TraceEvent(domain="example.com", node="discover", event_type="node_exit", summary="end")
    assert event_a.timestamp > 0
    assert event_b.timestamp >= event_a.timestamp


def test_required_fields_enforced():
    event = TraceEvent(
        domain="example.com",
        node="investigate",
        event_type="tool_call",
        summary="probed /api/jobs",
        artifact_ref="artifacts/example.com/probe_1.json",
        tokens_prompt=120,
        tokens_completion=45,
    )
    assert event.artifact_ref.endswith("probe_1.json")
    assert event.tokens_prompt == 120
