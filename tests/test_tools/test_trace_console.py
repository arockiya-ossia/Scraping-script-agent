from agent.trace.sink import TraceSink, _format_console_line


def test_node_enter_produces_a_transition_marker():
    line = _format_console_line({"type": "node_enter", "node": "discover"})
    assert line == ">>> discover"


def test_node_exit_is_silent():
    assert _format_console_line({"type": "node_exit", "node": "discover"}) is None


def test_decision_line_includes_action_and_rationale():
    line = _format_console_line(
        {"type": "decision", "node": "investigate", "action": "classify_ssr_html", "rationale": "found 4 links"}
    )
    assert "classify_ssr_html" in line
    assert "found 4 links" in line


def test_tool_result_error_is_flagged():
    line = _format_console_line({"type": "tool_result", "node": "investigate", "tool": "fetch_url", "error": "boom"})
    assert "ERROR" in line
    assert "boom" in line


def test_unknown_event_type_returns_none():
    assert _format_console_line({"type": "something_new", "node": "x"}) is None


def test_console_disabled_by_default(tmp_path, capsys):
    sink = TraceSink(trace_dir=tmp_path / "traces", artifacts_dir=tmp_path / "artifacts")
    sink.emit("example.com", "run1", type="decision", node="discover", action="x", rationale="y")
    captured = capsys.readouterr()
    assert captured.out == ""


def test_console_enabled_prints_to_stdout(tmp_path, capsys):
    sink = TraceSink(trace_dir=tmp_path / "traces", artifacts_dir=tmp_path / "artifacts", console=True)
    sink.emit("example.com", "run1", type="node_enter", node="discover")
    captured = capsys.readouterr()
    assert ">>> discover" in captured.out
