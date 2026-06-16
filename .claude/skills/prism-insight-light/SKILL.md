# prism-insight-light Development Patterns

Repository-specific guidance for agents working on `prism-insight-light`, a Python trading-execution runtime for GCP Pub/Sub signals and Korea Investment & Securities (KIS) order routing.

## Project Orientation

- Keep the fork lightweight: preserve the runtime focus on Pub/Sub ingestion, signal validation, KIS Korean/US order execution, market-hours checks, off-hours queueing, Docker helpers, and the small web UI.
- Do not reintroduce removed upstream scope such as analysis pipelines, broad reporting/orchestration, dashboards beyond the existing lightweight web UI, Firebase, Redis, or mobile integrations.
- Treat trading behavior as high impact. Prefer `demo` mode, `--dry-run`, fixtures, and mocks before any real account path.
- Never commit real credentials, KIS keys, account numbers, GCP service-account JSON, local `.env`, or live `trading/config/kis_devlp.yaml` values.

## Repository Map

| Path | Purpose |
| --- | --- |
| `subscriber.py` | Pub/Sub subscriber entrypoint and signal dispatch loop. |
| `trading/` | Core trading runtime: auth, market-hours logic, schema validation, sizing, domestic/US orders, queueing, Telegram fetch. |
| `trading/config/kis_devlp.yaml.example` | Safe example KIS configuration; keep live config out of git. |
| `webui/` | Lightweight Flask-style web UI, routes, services, templates, and static assets. |
| `tests/` | Regression tests for trading runtime, Docker helpers, readiness checks, and web UI behavior. |
| `check_pubsub_readiness.py`, `pubsub_readiness.py` | Pub/Sub/GCP readiness checks. |
| `install_prism_docker.sh`, `setup_subscriber*_crontab.sh` | Docker and cron helper scripts. |

## Coding Conventions

- Python files use `snake_case`; classes use `PascalCase`; constants use `SCREAMING_SNAKE_CASE`; functions should follow the existing surrounding module style.
- Prefer small, dependency-light modules that can be tested with mocked KIS/GCP boundaries.
- Keep imports explicit and local to the package where practical; avoid broad wildcard exports.
- Never wrap imports in `try`/`except`; handle optional runtime behavior at call sites instead.
- Keep user-facing README examples safe-by-default (`demo`, `--dry-run`, placeholder credentials).
- Preserve Korean/US market distinctions and account-specific behavior when touching order routing.

## Testing Workflow

Before committing code changes, run the narrow relevant tests plus the full suite when feasible:

```bash
python -m pytest
```

For targeted work, prefer focused commands such as:

```bash
python -m pytest tests/test_schema.py tests/test_dispatch.py
python -m pytest tests/test_webui_routes.py tests/test_webui_security.py
python -m pytest tests/test_market_hours.py tests/test_off_hours_policy.py
```

When changing shell installers, run their smoke tests:

```bash
python -m pytest tests/test_docker_installer_smoke.py
```

## Change Checklist

1. Identify the runtime boundary being changed: Pub/Sub, schema, KIS auth, domestic order, US order, sizing, queueing, web UI, Docker, or docs.
2. Add or update tests under `tests/` that mirror the affected module or behavior.
3. Use fixtures/mocks instead of network calls for KIS, GCP Pub/Sub, Telegram, or browser-dependent behavior.
4. Validate default behavior remains safe for `demo` and `--dry-run`.
5. Check docs/examples for credential leakage and safe defaults.
6. Commit with a concise, imperative message.

## Agent Collaboration

- Use read-only explorer agents for tracing unfamiliar execution paths before editing.
- Use reviewer agents for correctness, security, trading-safety, and regression review before finalizing.
- Use docs research agents only when validating current external APIs or release-note claims against primary sources.
- Share exact file paths, symbols, and test commands in handoffs so another agent can reproduce the reasoning.
