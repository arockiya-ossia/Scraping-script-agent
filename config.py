from dotenv import load_dotenv
import os
from pathlib import Path
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    # LLM
    freellmapi_base_url: str = os.environ["FREELLMAPI_BASE_URL"]
    freellmapi_api_key: str = os.environ["FREELLMAPI_API_KEY"]
    freellmapi_model: str = os.getenv("FREELLMAPI_MODEL", "auto")

    # Search
    serper_api_key: str = os.environ["SERPER_API_KEY"]
    serpapi_api_key: str | None = os.getenv("SERPAPI_API_KEY")
    firecrawl_api_key: str | None = os.getenv("FIRECRAWL_API_KEY")

    # Agent behavior
    max_repair_attempts: int = int(os.getenv("MAX_REPAIR_ATTEMPTS", "5"))
    max_total_attempts: int = int(os.getenv("MAX_TOTAL_ATTEMPTS", "8"))
    sandbox_timeout_seconds: int = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "120"))

    # Sandbox execution (Docker) — infra params, not code, so a resource
    # tweak never requires touching sandbox.py (same principle as no
    # hardcoded domains, CLAUDE.md §2 #2)
    sandbox_image: str = os.getenv("SANDBOX_IMAGE", "job-scraper-sandbox")
    sandbox_memory_limit: str = os.getenv("SANDBOX_MEMORY_LIMIT", "512m")
    sandbox_cpu_limit: str = os.getenv("SANDBOX_CPU_LIMIT", "1")
    sandbox_network: str = os.getenv("SANDBOX_NETWORK", "sandbox_net")
    egress_proxy_host: str = os.getenv("EGRESS_PROXY_HOST", "egress-proxy:3128")

    # Paths
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", "generated_scripts"))
    trace_dir: Path = Path(os.getenv("TRACE_DIR", "traces"))
    artifacts_dir: Path = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))


settings = Settings()
