# PRISM-INSIGHT Light repository guidance

Use \`.agents/skills/prism-insight-light/SKILL.md\` for project-specific patterns.

Autonomous maintenance skills use a pinned Git Submodule dependency:
- OMX: \`.agents/skills/autonomous-maintainer/SKILL.md\`
- Standalone: \`.agents/skills/autonomous-maintainer-standalone/SKILL.md\`
  (a local adapter reading the upstream
  \`.agents/skills/autonomous-maintainer/standalone/SKILL.md\`)

If the submodule has not been initialized, run
\`git submodule update --init --recursive\`. Verify with
\`python scripts/check_autonomous_maintainer_dependency.py\`.
Dependabot proposes reviewed updates of the pinned commit; updates do not
run the autonomous maintenance agent.

Only invoke repository-wide maintenance after explicit user authorization.
Preserve all finance, KIS, stop-loss, mock/demo, regression-test, fail-closed,
pre-PR approval, and deployment controls. Do not make live trades or merge
safety-critical changes without separate authorization. Never commit secrets,
account numbers, tokens, or live configuration.

See \`docs/AUTONOMOUS_MAINTAINER.md\` for dependency and update instructions.
