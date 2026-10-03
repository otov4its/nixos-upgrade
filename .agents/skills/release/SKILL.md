---
name: release
description: Use when the user asks to prepare, tag, or publish an official release of this nixos-upgrade repository, including release-version updates, stable-branch promotion, or release-tag publication.
---

# Release Workflow

This project-local skill is an entry point, not a second release procedure.
`RELEASE_CHECKLIST.md` is the source of truth for versions, commands, branch
flow, validation, and publication.

## Instructions

1. Confirm the user intends to carry out a release, not merely discuss release
   policy or edit release documentation.
2. Read `AGENTS.md` and the complete `RELEASE_CHECKLIST.md` before taking
   release actions. Consult `package.nix` and `CHANGELOG.md` when determining
   the current version or preparing the release notes.
3. Follow the checklist in order. Do not substitute remembered commands or
   invent alternate versioning, merge, tag, or push procedures. If this skill
   and the checklist appear inconsistent, follow the checklist and report the
   inconsistency.
4. Ask the user to choose any required release version, date, or next target
   that they have not specified. Never infer these values.
5. Stop when a checklist precondition fails, validation fails, a merge
   conflicts, or repository/remote state is unexpected. Preserve the existing
   work and refs; do not stash, discard, rewrite tags, force-push, or use broad
   `--all`/`--tags` operations to get past a blocker.
6. Obtain the checklist's explicit approval for the exact tag and commit before
   creating the tag, and separate explicit approval for the exact refs before
   pushing. A deadline or earlier approval for a different operation does not
   replace either approval.
7. Do not perform system activation or mutate the host while preparing or
   validating a release. Use the checklist's Nix validation commands only.
8. Report the release version, validated commit, tag, refs published, and
   validation results. If publication is rejected or partial, report the exact
   remote state and stop; do not attempt an unapproved recovery.
