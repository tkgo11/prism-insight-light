# Autonomous Maintainer skill integration

This repository includes **two project-local Codex skills** mirrored from
[tkgo11/autonomous-maintainer](https://github.com/tkgo11/autonomous-maintainer):

- `.agents/skills/autonomous-maintainer/`: OMX orchestration (`$autonomous-maintainer`)
- `.agents/skills/autonomous-maintainer-standalone/`: built-in Codex workflow (`$autonomous-maintainer-standalone`)

Use the standalone variant unless the agent environment has Oh My Codex installed.
The snapshot source, full upstream commit and file SHA-256 digests are recorded in
`.agents/skills/.autonomous-maintainer-source.json`.

## Safety and scope

Installing the skills does **not** start autonomous maintenance. Invoke a skill
only for an explicitly authorized repository-wide maintenance request. PRISM's
trading-safety contracts, demo/dry-run defaults, stop-loss safeguards, test gates,
branch policies and approval requirements remain mandatory. Skills do not authorize
placing live trades, committing secrets, disabling tests, merging trading PRs, or
starting unattended live trading.

## Automatic updates

`.github/workflows/sync-autonomous-maintainer.yml` checks the upstream `main`
branch each day at 04:17 UTC and supports manual `workflow_dispatch` runs.

When the tracked upstream version changes, the workflow validates source SHA-256
digests against upstream `CHECKSUMS.txt`, copies only the six skill resources,
verifies the installed snapshot, runs local updater regression tests, and opens a
dedicated GitHub pull request. It never executes downloaded upstream scripts or
edits trading code. If existing skill files were modified outside the updater, it
**fails closed** rather than overwriting them.

The workflow **does not auto-merge**: review and required repository checks must
pass before merging. GitHub Actions must be allowed to create pull requests in the
repository settings; otherwise the workflow can push its branch but GitHub will
reject the PR creation. See Settings → Actions → General → Workflow permissions →
“Allow GitHub Actions to create and approve pull requests”. PR-created workflow
runs may require approval depending on the account's Actions settings.

For local/manual updates:

```bash
git clone --depth 1 https://github.com/tkgo11/autonomous-maintainer.git /tmp/prism-maintainer-source
python3 scripts/sync_autonomous_maintainer.py \
  --source /tmp/prism-maintainer-source \
  --revision "$(git -C /tmp/prism-maintainer-source rev-parse HEAD)"
python3 scripts/sync_autonomous_maintainer.py --verify-installed
python3 tests/test_autonomous_maintainer_sync.py -q
```

The updater is not an AI agent; it only refreshes the Codex skill definitions.
