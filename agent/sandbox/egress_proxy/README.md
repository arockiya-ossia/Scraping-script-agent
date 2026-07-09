# Egress Proxy Sidecar

Enforces the network policy in CLAUDE.md §8: the sandbox container has no
direct internet route, only a path through this proxy.

**Implementation**: Squid (`Dockerfile`, `squid.conf`), started via
`docker-compose.yml` at the repo root before any sandbox run.

Allow: public IPs. Deny (`squid.conf` ACLs, checked first — deny wins):
`169.254.169.254/32` (cloud metadata), `10.0.0.0/8`, `172.16.0.0/12`,
`192.168.0.0/16`, loopback, link-local.

The policy is data (`squid.conf`), not code — change the file, no rebuild
of `agent/tools/sandbox.py` or the scraper image needed.

## Wiring

- `docker-compose.yml` defines `sandbox_net` as `internal: true` — no
  default route to the internet for anything on that network.
- `egress-proxy` service joins both `sandbox_net` and the default bridge
  (which does have internet access), so it's the only bridge between them.
- `sandbox.py::run_script` attaches each scraper container to `sandbox_net`
  and sets `HTTP_PROXY`/`HTTPS_PROXY` to `egress-proxy:3128` (both
  configurable via `.env` — `SANDBOX_NETWORK`, `EGRESS_PROXY_HOST`).

## Start it

```bash
docker compose up -d egress-proxy
```

Must be running (and `sandbox_net` therefore created) before
`agent/tools/sandbox.py::run_script` is called — `docker run --network
sandbox_net` fails if the network doesn't exist yet.
