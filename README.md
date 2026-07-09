# job-scraper-agent

AI agent that writes job-scraper scripts. Full spec: [CLAUDE.md](CLAUDE.md).

## Status

Scaffolded per CLAUDE.md §13 milestones 1-2, plus a stubbed LangGraph
skeleton (milestone 5) with routing/budget logic validated in isolation
(`tests/test_graph_routing.py`). `discover`, `investigate`,
`generate_script`, and `docker_execute` are currently **stubs** that return
fixed data — real search/fetch/LLM/sandbox wiring is not yet implemented
(milestones 6-8).

## Setup

```bash
python -m venv venv
source venv/Scripts/activate   # Windows Git Bash
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env           # fill in real API keys
```

## Run

```bash
python scripts/run_agent.py --domain swissre.com
```

## Test

```bash
pytest tests/ -q
```
