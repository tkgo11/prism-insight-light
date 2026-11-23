# Autonomous Maintainer (Git Submodule dependency)

The authoritative skill source is the public repository
[tkgo11/autonomous-maintainer](https://github.com/tkgo11/autonomous-maintainer).

This project uses **one pinned Git Submodule**, rather than copying upstream
skill content or running a custom synchronization workflow:

- `.agents/skills/autonomous-maintainer/` is a Git Submodule with the **OMX**
  `SKILL.md` and its complete upstream package.
- `.agents/skills/autonomous-maintainer-standalone/SKILL.md` is a small,
  platform-neutral adapter that loads the original
  `../autonomous-maintainer/standalone/SKILL.md` from that same submodule.
  Its local UI metadata/icon are kept for Codex skill discovery.
- Both variants follow the **same pinned upstream commit**. The current commit
  is recorded by the parent repository's gitlink, not a copied version manifest.

## Clone and initialize

```bash
git clone --recurse-submodules https://github.com/tkgo11/prism-insight-light.git
```

For an existing checkout, run:

```bash
git submodule update --init --recursive
python scripts/check_autonomous_maintainer_dependency.py
```

A plain `git clone` without submodule initialization will not populate the
skill and Codex will not be able to use it. Do not use
`git submodule update --remote` as an unattended deployment mechanism; it
advances the gitlink without a reviewable dependency PR.

## Automatic updates

GitHub **Dependabot** checks the Git Submodule daily according to
`.github/dependabot.yml` (04:00 Asia/Seoul). When upstream `main` advances,
Dependabot can propose a normal pull request to update the **one pinned gitlink**.
This does not require GitHub Actions write permissions or an API key.

CI performs a recursive submodule checkout, checks the pinned commit, validates
upstream skill identities and SHA-256 checksums, and runs the repository tests.
The old daily file-copying GitHub Actions workflow is removed.

Dependency updates are **not automatically merged** by this integration.
Review the incoming upstream changes, validate the relevant CI checks, and
merge the update PR only after review. A Git Submodule is pinned to a specific
commit; simply updating upstream does not change this repository until that
dependency PR is merged.

## Trading safety

Installing these skills does not launch an autonomous agent run.
Use the standalone variant unless Oh My Codex is available. Any agent must
explicitly receive a repository-wide maintenance request and must preserve
accepted PRISM trading-safety behavior, stop-loss guarantees, fail-closed order
handling, demo/dry-run defaults, secrecy controls, and required approvals.
No skill or dependency update authorizes live trades, trading deployments, or
automatically merging finance-related changes.
