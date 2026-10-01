# Current Review and Follow-ups

Date: 2026-09-29
Branch: `agents/improvements`

This is the working status for future review work. The original `REVIEW.md` is
left unchanged as the historical audit; its references to the former root
worker and IPC protocol are not current implementation guidance.

## Current architecture

The Bash launcher blocks the supported signals during Python startup. The
Python controller runs as the invoking user and handles CLI parsing, the
per-user singleton lock, Nix update/build, diffing, and confirmation. After a
positive confirmation, it invokes the fixed-purpose activation helper through
`sudo`. The helper switches the system and, after a successful switch,
publishes the temporary lock file and optionally commits as the repository
owner.

## Resolved or obsolete findings

- **A.1–A.5:** These findings describe the removed privileged worker, copied
  flake tree, and Python/Bash IPC protocol. That worker and protocol no longer
  exist; the flake is passed directly to Nix and the activation helper does not
  delete a path obtained from `git rev-parse`.
- **A.6:** The listed Python and CLI defects have been addressed: signal policy
  uses callable functions, logging configures `logging.raiseExceptions`, NVD
  status markers drive package-change classification, and `-y`/`-n` are
  mutually exclusive. The launcher blocks the chosen default-action signals:
  HUP, INT, QUIT, USR1, USR2, TERM, PIPE, XCPU, XFSZ, and ALRM; synchronous
  faults and real-time signals are excluded.
- **A.7:** The old mismatch where untracked files could be built but omitted
  from the commit is removed by the selected Git-indexed source policy. The
  activation helper uses the repository owner's Git identity and avoids empty
  commits.
- **A.8:** The controller now uses a per-user lock in `XDG_RUNTIME_DIR`, not a
  version-specific Nix store path.
- **B.1–B.3:** The one-shot sudo activation design is implemented; direct Git
  worktree source semantics were selected; and the `nix flake show` pre-check
  was removed.
- **B.4:** `CliProgram` is now a thin application controller composed from
  typed options, `Console`, `CommandRunner`, and `NixWorkflow`; startup and
  resource lifetime live in `main(argv)`.
- **B.6:** Spinner output uses an explicitly selected stream and is disabled
  for non-interactive or dumb terminals.
- **B.8, except the versioning follow-up below:** The package overlay and NixOS
  module use host packages by default; module options use `mkEnableOption` and
  `mkPackageOption`; Nix experimental features are scoped to the tool's own
  commands; outputs are Linux-only; packaging uses `--replace-fail`; and the
  static-analysis and development-bytecode cleanups are in place.
- **B.9:** Configuration selection is available as `-C, --configuration NAME`;
  selective lock updates are available as `--inputs NAME [NAME ...]`.

## Accepted decisions and known trade-offs

- **Git-indexed source:** Nix sees Git-indexed files, including modifications
  to tracked files, but not untracked or ignored files. New files must be added
  to the Git index before upgrading. This is the selected behavior.
- **Source can change between Nix commands:** lock update and build are separate
  invocations against the worktree. If tracked files change between them, the
  two invocations may observe different contents. No source snapshot or
  worktree lock currently prevents this; decide whether that residual risk is
  acceptable before attempting a snapshot/locking design.
- **Auto-commit uses `git commit --all`:** all modified tracked files may be
  committed, not just files intentionally changed for the upgrade. This was
  retained deliberately because automatic commits of tracked package changes
  are useful. Untracked files are not included and are not part of the selected
  Nix source until indexed.
- **Deferred signal handling:** `synsignals` queues application signals and
  handles them at safe points. This is retained intentionally to avoid
  interrupting critical work; the simpler exception-based handler proposed in
  the old B.5 is not the chosen design.
- **B.7 Python tooling (accepted):** Use Ruff's conservative lint rules plus
  McCabe `C901` with a maximum complexity of 13, and BasedPyright's basic mode.
  Enforce Ruff formatting through Treefmt; additional rules remain optional.
- **B.8 version convention (accepted):** Use SemVer with the date as build
  metadata: `X.Y.Z+YYYYMMDD` for a release and `X.Y.Z-rc+YYYYMMDD` for a
  release candidate (for example, `2.0.0-rc+20260929`). `package.nix` is the
  package version source; the release checklist requires the matching changelog
  entry. No Git-derived development version is planned.

## Remaining follow-ups

No outstanding follow-ups from this review. The optional B.8 development-version
source remains a consciously accepted convention rather than planned work.

## Review-file maintenance

Use this file for future work on the remaining follow-ups. Do not treat the old
`REVIEW.md` line numbers, old worker references, or its former “currently none”
testing statement as current. Update this status when a follow-up is completed
or a trade-off is reconsidered.
