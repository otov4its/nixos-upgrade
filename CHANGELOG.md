# Changelog

## Unreleased

* Modularize the Python CLI into typed options, an isolated terminal console,
  a subprocess runner, a Nix workflow with typed outcomes, and a thin application
  controller, without changing the CLI or runtime behavior.
* Add a shared Treefmt configuration for Nix, Python, Bash, TOML, Markdown,
  and JSON/JSONC; expose it through `nix fmt` and enforce it in `nix flake check`.
* Remove YAPF from the development environment in favor of Ruff formatting.

* Use SemVer release versions with date build metadata and `-rc` pre-release
  versions (for example, `2.0.0-rc+20260929`).
* Add `-C, --configuration NAME` to select a NixOS flake configuration; default
  to the current hostname.
* Add `--inputs NAME [NAME ...]` to update selected flake inputs; omitting it
  still updates all inputs, and it conflicts with `--no-update-lock-file`.
* Delegate CLI parsing and the per-user singleton lock to Python; keep the
  Bash launcher focused on runtime setup and blocking signals before Python
  starts.
* Run the controller and Nix preparation as the invoking user; request sudo
  only after upgrade confirmation to run a fixed-purpose activation helper.
  `--assume-yes` does not bypass sudo authorization.
* Build directly from the Git worktree so Nix uses Git-indexed source files
  and no copied flake tree is created.
* Reject conflicting `--assume-yes` and `--assume-no` options.
* Classify package changes using NVD status markers instead of arbitrary
  bracketed text.
* Extract NVD diff parsing and summary formatting into pure, independently tested
  functions.
* Configure yaspin's output stream explicitly instead of redirecting `sys.stdout`.
* Keep the spinner monochrome when color is disabled, and disable it on dumb
  or non-interactive terminals.
* Configure logging exception handling through `logging.raiseExceptions`.
* Represent signal preservation policies as typed callable functions.
* Limit deferred signal handling to the application's supported termination signals.
* Use the repository owner's Git identity for auto-commits and avoid empty commits.
* Use a per-user runtime lock independent of the Nix store worker path.
* Add a reusable Nixpkgs overlay and standalone package expression; the NixOS
  module defaults to the host package and supports explicit package selection
  or opting out with `null`.
* Keep NixOS `nix.settings.experimental-features` unchanged; the tool requests
  `nix-command` and `flakes` only on its own Nix invocations.
* Limit flake package, dev-shell, and check outputs to Linux systems.
* Make package builds fail if an expected source placeholder is missing.
* Skip Python bytecode compilation in the development package while retaining
  optimized bytecode in the default package.
* Run `statix` and `deadnix` in the flake's checks.
* Replace Pylsp/Pyflakes with Ruff and BasedPyright, and run Python lint/type
  checks, unit tests, shell regressions, ShellCheck, and bytecode-policy checks
  through `nix flake check`.
* Make standard-stream detection safe when a stream is unavailable, and handle
  optional subprocess output explicitly.
* Make dynamic module stubs and import-spec handling in Python tests compatible
  with BasedPyright.

## 2026-07-01-1.0.5

* Fixed 'system' has been renamed to/replaced by 'stdenv.hostPlatform.system' evaluation warning
* Fixed nested list in attribute 'nativeBuildInputs' warning

## 2026-07-01-1.0.4

* Bumped versions

## 2024-12-16-1.0.3

* Fixed "File descriptor leaked on lvs invocation" warnings

## 2024-12-10-1.0.2

* A more informative message on drop privileges to 'nobody' error
* `flake.nix` `inputs` to 24.11 NixOs
* Fixed zellij dev layout session arg
* Added `--flake` arg to `nix flake update` due to error of the new `nix` version

## 2023-11-18-1.0.1

* Added `-V`, `--version` option to man page
* Added `dev` package with dev python options
* Code cleanups and refactoring

## 2023-11-16-1.0.0

* Happy Birthday
