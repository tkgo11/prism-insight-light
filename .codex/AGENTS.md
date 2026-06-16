# ECC for Codex CLI

This supplements any root `AGENTS.md` with repo-local guidance for `prism-insight-light`.

## Repository Skill

- Codex-facing skill: `.agents/skills/prism-insight-light/SKILL.md`
- Claude-facing companion skill: `.claude/skills/prism-insight-light/SKILL.md`
- The skill is the canonical agent summary for architecture, safety defaults, testing, and collaboration patterns.

## Safety Baseline

- Keep this fork focused on trading execution runtime only; do not reintroduce removed analysis/reporting/orchestration scope.
- Treat all trading paths as high impact. Prefer `demo`, `--dry-run`, mocks, and fixtures before real-account behavior.
- Keep credentials out of git: no KIS secrets, account numbers, GCP service-account JSON, `.env`, or live `trading/config/kis_devlp.yaml`.
- Keep user-specific credentials and private MCPs in `~/.codex/config.toml`, not in this repository.

## MCP Baseline

Treat `.codex/config.toml` as the default ECC-safe baseline for work in this repository. The generated baseline enables GitHub, Context7, Exa, Memory, Playwright, and Sequential Thinking.

## Multi-Agent Support

- Explorer: read-only evidence gathering for unfamiliar execution paths.
- Reviewer: correctness, security, trading-safety, and regression review.
- Docs researcher: primary-source API and release-note verification.
- Test planner: read-only test strategy and command selection for risky changes.

## Workflow Expectations

1. Trace the affected path before editing.
2. Make minimal, runtime-focused changes.
3. Add or update tests for changed behavior.
4. Run focused tests plus `python -m pytest` when feasible.
5. Cite exact files, symbols, and test commands in final handoffs.
