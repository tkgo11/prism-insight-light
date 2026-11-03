# PRISM-INSIGHT Light repository guidance

Use `.agents/skills/prism-insight-light/SKILL.md` for project conventions.

The project-local Codex maintenance skills are:
- `.agents/skills/autonomous-maintainer/SKILL.md` (OMX variant)
- `.agents/skills/autonomous-maintainer-standalone/SKILL.md` (standalone variant)

Invoke a repository-wide maintenance skill only after explicit user authorization.
Skill synchronization only installs skill instructions; it must not launch an
autonomous maintenance run. For all skill-guided tasks, prioritize accepted
trading-safety contracts and repository protections over broad rewrites.

Do not place live orders, deploy trading changes, disable stop-loss protections,
weaken tests, or merge high-impact changes without the established safety gates.
Use fixtures, mocks, demo mode, and dry-run before any real-account behavior.
Never commit KIS/GCP secrets, account identifiers, tokens or live configuration.

Run relevant tests and review all affected consumer paths. Record failures and
limitations accurately. See `docs/AUTONOMOUS_MAINTAINER.md` for auto-updates.
