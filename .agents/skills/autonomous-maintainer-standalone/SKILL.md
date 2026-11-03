---
name: autonomous-maintainer-standalone
description: "Run the complete Codex-native autonomous repository maintenance workflow from the pinned tkgo11/autonomous-maintainer Git submodule, with explicit activation and all upstream verification and pre-PR approval gates."
---

# Autonomous Maintainer — Standalone (dependency adapter)

This skill is an adapter for the single Git Submodule dependency. **The complete,
authoritative instructions are not duplicated in this repository.**

When this skill is invoked, first read and apply **all** of the instructions in
the file at `../autonomous-maintainer/standalone/SKILL.md` relative to this
skill directory. That path is
`.agents/skills/autonomous-maintainer/standalone/SKILL.md` from the project root.

If that file is missing, stop without implementing changes and tell the user to
run `git submodule update --init --recursive`. Do not treat this adapter as
a substitute for the upstream instructions, and do not guess their contents.

The upstream skill's explicit activation, contract preservation, convergence,
verification, safety, user inspection, and pre-PR approval requirements apply
in full. Follow the repository's `AGENTS.md` and trading-safety gates.
