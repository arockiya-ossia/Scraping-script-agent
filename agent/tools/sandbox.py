"""Docker execution wrapper — builds/runs the sandbox image, enforces the
wall-clock timeout, and captures stdout/stderr/output file (CLAUDE.md §8).
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from config import settings

DOCKERFILE_DIR = Path(__file__).resolve().parent.parent / "sandbox"


@dataclass
class SandboxRunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    output_path: Path


def build_image() -> None:
    subprocess.run(
        ["docker", "build", "-t", settings.sandbox_image, str(DOCKERFILE_DIR)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def run_script(script_path: Path, output_dir: Path, domain: str) -> SandboxRunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "output.jsonl"

    proxy_url = f"http://{settings.egress_proxy_host}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        f"scraper-{domain.replace('.', '-')}",
        "--network",
        settings.sandbox_network,  # internal-only network; egress only via proxy sidecar
        "-e",
        f"HTTP_PROXY={proxy_url}",
        "-e",
        f"HTTPS_PROXY={proxy_url}",
        "-e",
        f"http_proxy={proxy_url}",
        "-e",
        f"https_proxy={proxy_url}",
        "-e",
        "HOME=/tmp",  # writable scratch for Chromium's profile/cache dirs (root fs is read-only)
        "--memory",
        settings.sandbox_memory_limit,
        "--cpus",
        settings.sandbox_cpu_limit,
        "--shm-size",
        settings.sandbox_shm_size,  # Chromium needs >64MB /dev/shm or it crashes
        "--read-only",
        "--tmpfs",
        "/tmp",
        "-v",
        f"{script_path.resolve()}:/app/scraper.py:ro",
        "-v",
        f"{output_dir.resolve()}:/output",
        settings.sandbox_image,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.sandbox_timeout_seconds,
        )
        return SandboxRunResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
            output_path=output_path,
        )
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "kill", f"scraper-{domain.replace('.', '-')}"], capture_output=True)
        return SandboxRunResult(
            exit_code=-1,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + "\n[sandbox] wall-clock timeout exceeded",
            timed_out=True,
            output_path=output_path,
        )
