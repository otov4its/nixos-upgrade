# ADR 0002: Use Git-indexed flake sources and owner-context auto-commits

- Status: Accepted
- Date: 2026-10-03

## Context

Nix treats a local path inside a Git repository as a Git flake. Copying the
repository to a temporary directory obscured which files were used for the
build and created cleanup risks. The tool also intentionally commits package
changes after a successful system switch, but the built source and committed
files must follow compatible inclusion rules.

## Decision

Pass the resolved flake directory directly to Nix. Nix sees the current
contents of Git-tracked files, including modifications, but excludes untracked
and ignored files. New files must be added to the Git index before they can be
used as flake inputs. Do not copy the flake tree or snapshot it between the
lock-update and build commands.

After a successful switch, publish a requested lock-file update and, unless
`--no-commit` is set, commit with `git commit --all` as the repository owner.
Avoid empty commits.

## Consequences

- An untracked or ignored file cannot silently affect the build while being
  omitted from the post-upgrade commit.
- Lock update and build are separate worktree invocations. Tracked source can
  change between them, so they may observe different contents; the worktree is
  not snapshotted or locked across both commands.
- `git commit --all` can include any modified tracked files, not only changes
  intended for the upgrade. This behavior is retained because automatically
  committing tracked package changes is useful; `--no-commit` opts out.
- Untracked files are neither part of the selected Git flake source nor included
  by `git commit --all`.

## Alternatives considered

- Copy the whole directory and build from the copy. Rejected because it creates
  a second, potentially misleading source tree and complicates safe cleanup.
- Include every untracked file automatically. Rejected because it would make
  the source boundary less explicit and could build files not intended for the
  repository.
