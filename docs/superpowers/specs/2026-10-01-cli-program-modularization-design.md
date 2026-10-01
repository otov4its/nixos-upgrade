# Python CLI modularization design

## Goal

Split the responsibilities currently concentrated in `src/lib/nixos-upgrade.py`
into a small set of cohesive modules. Improve test isolation and make process,
terminal, and upgrade-workflow behavior understandable independently, while
preserving the existing CLI and runtime behavior.

The refactor is behavior-preserving. It does not change the privilege boundary,
Nix source semantics, activation protocol, output conventions, or signal policy.

## Approach selection

- **Minimal extraction:** move only argument parsing and console setup, leaving
  subprocess lifecycle and Nix workflow in the controller. This has the smallest
  initial diff, but leaves the most coupled responsibilities together.
- **Thin controller with small services (selected):** separate CLI options,
  console behavior, subprocess lifecycle, and Nix preparation while keeping the
  existing activation, NVD, formatter, and signal modules. This improves focused
  testing without turning each method into a class or introducing broad
  interfaces.
- **Full layered redesign:** introduce more general ports/adapters and dependency
  injection across the whole application. That would add abstraction and scope
  without a current need for plugin support or multiple implementations.

## Current responsibilities

`CliProgram` currently combines:

- argument parsing and color-option environment setup;
- logger, spinner, and terminal-stream configuration;
- per-user singleton locking, temporary paths, and current-system lookup;
- child-process creation, output routing, polling, and signal-driven cleanup;
- flake validation, lock-file preparation/update, Nix build, and closure diff;
- confirmation, privileged activation invocation, reporting, and process exit.

The codebase already has useful boundaries that should remain: `activation.py`
encodes and validates the privileged-helper protocol, `nvd.py` contains diff
parsing and summaries, `colorformatter.py` formats colored log records, and
`synsignals.py` implements deferred signal handling.

## Design principles

1. Keep the refactor incremental and behavior-preserving; existing shell-level
   workflow tests remain the end-to-end contract.
2. Give modules responsibilities with explicit inputs and outputs. Do not move
   methods unchanged if they still depend on the entire `CliProgram` instance.
3. Runtime service modules must not call `sys.exit`; the application controller
   maps errors and results to user messages and exit statuses. The CLI adapter
   may preserve `argparse`'s standard early `SystemExit` for `--help` and
   `--version`.
4. Avoid introducing abstractions for every method. Keep small resource setup in
   the application until testing demonstrates that it needs its own boundary.
5. Add no new runtime dependencies. New Python files remain alongside the
   existing modules in `src/lib` and are included by the package's existing
   recursive source copy.

## Proposed modules

### `cli_options.py`

Own `ColorOption`, an immutable `Options` data structure, and argument parsing.
The parser receives runtime defaults rather than reading environment variables
at module import time. It preserves all existing options, defaults, mutual
exclusion rules, help/version behavior, and custom usage-error presentation.
`argparse` may raise its normal `SystemExit` for help/version; other parse
failures are returned or raised as a structured CLI error for the application
to display through `Console`. The parse result/error retains enough valid
verbosity and color settings to preserve current usage-error presentation.
Parsing and option validation do not acquire locks, create temporary files, or
start subprocesses.

### `console.py`

Own terminal-facing behavior: stream/TTY and color decisions, logger setup,
spinner lifecycle, confirmation prompts, and user-facing status/reporting. It
uses `colorformatter.py` rather than duplicating its formatting logic. It
provides an explicit interface for starting/stopping progress and for displaying
messages; business/workflow code does not directly manipulate terminal streams.

### `command_runner.py`

Own subprocess creation and lifecycle: process-group setup, stdout capture,
output forwarding, environment construction, polling, and the active-child
reference needed for signal cleanup. It uses a narrow progress/logging interface
from `Console`, not the application controller. Replace the overloaded
`stderr_out` flag with an explicit output policy that preserves the current two
modes: merge child stderr into captured stdout, or inherit stderr so commands
such as Nix can show native progress. The runner returns a
`CompletedProcess`-style result for nonzero exit statuses and never decides the
application's final exit status. Spawn failures remain exceptions for the
application boundary to handle.

The controller's signal callback can ask the runner to terminate its active
process group using the existing TERM / grace-period / KILL behavior. The runner
owns the process handle and waits for termination; the controller remains
responsible for logging the signal and exiting with the existing signal-derived
status. `synsignals.py` remains the deferred/synchronous signal mechanism.

### `nix_workflow.py`

Own the unprivileged Nix preparation workflow: validate the flake directory,
prepare or update the temporary lock file, build the selected NixOS
configuration, resolve the built closure, run the NVD comparison, and return a
typed outcome: `NoChanges`, or an `UpgradeCandidate` containing the relevant
closures, raw diff, and change counts. It constructs Nix arguments and uses
`command_runner.py` to execute them; it does not print user-facing output or exit
the process. It uses `nvd.py` to count changes; the controller calls
`nvd.format_diff` and `nvd.format_change_summary` to display the diff and summary.

The temporary lock path and result link remain application-owned resources in
the first pass. They may be grouped in a small workspace object if that makes
the workflow interface clearer, but a new general-purpose resource framework is
out of scope.

### `nixos-upgrade.py`

Remain the entry point and contain the `CliProgram` application controller. Its
responsibilities are startup/resource lifetime, supported-signal registration,
workflow sequencing, showing the diff, confirmation, calling the existing
`activation.py` protocol for sudo activation, and translating workflow or
activation results into final messages and exit statuses.

Move runtime environment reads such as `NAME` and `VERSION` from class/module
initialization into startup configuration. Preserve the existing per-user lock
lifetime, temporary-directory lifetime, and privileged-activation signal
blocking behavior.

## Runtime flow

1. `main` obtains runtime defaults and parses arguments before acquiring runtime
   resources; `--help` and `--version` continue to exit before lock/workspace
   setup.
2. The application configures `Console`, acquires the singleton lock, creates
   the temporary Nix workspace, and reads the current system closure.
3. The application asks `NixWorkflow` to validate the flake, perform the
   requested lock update, build the selected configuration, and compare the
   current and candidate closures.
4. The application presents the diff and asks for confirmation through
   `Console`. A decline or no-change outcome does not invoke sudo.
5. After confirmation, the application invokes the existing fixed-purpose
   activation flow. It reports the structured activation result and selects the
   existing success/error exit behavior.
6. On a supported signal during an ordinary child command, the application
   reports the signal and asks `CommandRunner` to terminate and reap the child.
   During privileged activation, existing blocked/deferred handling remains in
   force until the helper returns and its result is processed.

## Error and output behavior

- Runtime service modules return structured results or raise ordinary
  exceptions; they do not terminate the process. The CLI adapter preserves
  `argparse`'s normal early exit for help and version, while the application
  handles workflow failures and selects the final process exit status.
- A nonzero child status remains available to the workflow so lock-update,
  build, and comparison failures retain their current, distinct handling.
- Subprocess stderr behavior remains explicit and unchanged: merged output is
  captured and processed by the application, while inherited stderr preserves
  native child progress.
- CLI help/version, confirmation behavior, logging colors, spinner behavior,
  child termination timeout, and exit-code mapping remain compatible.

## Testing

- Keep and adapt the current parser/startup tests for option defaults, help and
  version, validation, and startup ordering.
- Add focused runner tests for merged versus inherited stderr, captured stdout,
  environment/color propagation, return codes, and TERM-to-KILL cleanup.
- Test `NixWorkflow` with a fake runner and temporary workspace, asserting
  generated Nix arguments and outcomes without invoking real Nix builds.
- Test controller confirmation and activation decisions with fake workflow and
  activation results.
- Retain `tests/test-user-workflow.sh` and the existing signal, spinner,
  singleton, logging, flake-source, auto-commit, shell, and Python tests as
  integration/regression coverage.
- Run `nix flake check` after each extraction stage and at completion.

## Non-goals

- No changes to user-visible CLI semantics or output, installed entrypoint or
  runtime behavior, Nix commands/source semantics, privilege separation, sudo
  helper contract, or activation policy.
- No rewrite of `activation.py`, `nvd.py`, `colorformatter.py`, or
  `synsignals.py` beyond adjustments required to maintain their existing
  interfaces.
- No new third-party dependencies, asynchronous framework, plugin system, or
  class-per-method decomposition.
- No attempt to expand Ruff/BasedPyright policies as part of this refactor.

## Implementation sequence

1. Extract CLI options and remove import-time environment access while keeping
   the existing startup and parser tests green.
2. Extract `CommandRunner`; lock down output and signal cleanup semantics with
   focused tests before changing workflow callers.
3. Extract `NixWorkflow` and its result data, retaining the current Nix command
   arguments and lock-file behavior.
4. Reduce `CliProgram` to startup and orchestration, adapt focused tests, and
   keep the full workflow integration tests unchanged in intent.
5. Run the full flake check, review the diff for behavior-only changes, and
   update `REVIEW-NEXT.md` to mark the modularization follow-up complete.
