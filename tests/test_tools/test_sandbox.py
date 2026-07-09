import subprocess

from agent.tools import sandbox
from config import settings


def test_run_script_cmd_uses_config_not_literals(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)

    script_path = tmp_path / "scraper.py"
    script_path.write_text("print('hi')")
    output_dir = tmp_path / "out"

    sandbox.run_script(script_path, output_dir, "example.com")

    cmd = captured["cmd"]
    assert settings.sandbox_image in cmd
    assert settings.sandbox_network in cmd
    assert settings.sandbox_memory_limit in cmd
    assert settings.sandbox_cpu_limit in cmd
    assert f"HTTP_PROXY=http://{settings.egress_proxy_host}" in cmd
    assert f"HTTPS_PROXY=http://{settings.egress_proxy_host}" in cmd
