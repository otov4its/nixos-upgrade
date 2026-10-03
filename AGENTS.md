# Agent instructions

## Start here

- Read [`docs/architecture.md`](docs/architecture.md) for the current system
  flow and [`docs/decisions/`](docs/decisions/) for the rationale behind major
  choices. Source code and tests define exact behavior.
- Use `README.md` for usage and development setup, and `RELEASE_CHECKLIST.md`
  for release tasks.

## Safety and behavior contracts

- Keep CLI orchestration, Nix preparation, diffing, and confirmation as the
  invoking user. Only the fixed activation helper may run through `sudo` after
  the application proceeds with an upgrade. `--assume-yes` never bypasses sudo
  authorization; never pass arbitrary commands or shell text to the helper.
- Do not run a real system activation, profile mutation, or
  `switch-to-configuration` against the host for validation. Use the controlled
  tests and NixOS VM test.
- Preserve the Git-flake source policy: modified tracked files are visible to
  Nix, while untracked and ignored files are not. Do not assume a flake check
  included a new untracked file, and do not stage unrelated user files to make a
  check see them.
- `git commit --all` can include every modified tracked file, not only changes
  related to the upgrade. Treat this as intentional behavior when changing
  auto-commit or source-selection logic.
- Preserve safe-point signal handling and child-process cleanup. Changes to
  privilege boundaries, activation ordering, source selection, signals, or
  auto-commit need focused regression coverage.

## Development and validation

- Use `nix develop` for the project development environment.
- Run `nix flake check` for the full test and static-analysis suite.
- Run `nix fmt -- --fail-on-change <paths>` to check formatting for changed
  files. Formatter and Python lint settings live in `treefmt.toml` and
  `pyproject.toml`.
- Keep runtime dependencies in `package.nix` and flake/development/check
  dependencies in `flake.nix`. Check closure-size costs before adding tooling;
  Mermaid CLI is intentionally not a check dependency because it adds roughly
  1.5 GiB. Validate Mermaid changes with a compatible preview unless this choice
  is revisited.

## Change hygiene

- Preserve unrelated user changes; do not stage or commit unless requested.
- Before a requested commit, add an appropriate `Unreleased` entry to
  `CHANGELOG.md`.
