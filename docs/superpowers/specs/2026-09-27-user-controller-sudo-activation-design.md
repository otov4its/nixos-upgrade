# Unprivileged controller with sudo activation design

## Goal

Keep the application controller running as the invoking user, and request root
privileges only after the user has reviewed the proposed system diff and
confirmed the upgrade. Use `sudo` to launch one fixed-purpose, one-shot helper
for system activation and the post-activation repository updates that may need
to run as the repository owner.

Preserve the existing default flake path (`/etc/nixos`), Git-indexed source
semantics, lock-file update option, `--assume-yes` / `--assume-no` behavior,
owner-identity auto-commit policy, and opt-out via `--no-commit`. In particular,
`--assume-yes` skips the application confirmation only; it does not bypass
sudo authorization.

## Current behavior

`src/bin/nixos-upgrade` currently performs setup as root, starts
`src/lib/privileged-worker` as a long-lived root process, then uses `setpriv` to
run the Python UI/controller as `nobody`. The two processes communicate over
inherited file descriptors using a delimiter-based command protocol. The root
worker currently performs Nix update/build operations as well as system
activation, lock-file publication, and repository-owner Git commits.

The package fixes a runtime `PATH` and substitutes the worker and Python script
paths in `package.nix`. Existing shell tests exercise parts of the worker
protocol and assert details of its commit policy.

## Proposed runtime flow

1. The user launches `nixos-upgrade` normally, without `sudo`. The launcher
   establishes the packaged runtime environment and execs Python as the
   invoking user. It does not start a root worker, drop Python to `nobody`, or
   create worker IPC pipes.
2. Python holds the existing per-user singleton lock, creates and owns a
   temporary work directory for Nix lock/build outputs, resolves and validates
   the flake path, and reads the current `/run/current-system` closure.
3. Python runs `nix flake update` with `--output-lock-file` targeting its
   temporary directory, when updates are enabled. It runs the build as the
   invoking user, using the selected temporary lock file as a reference and
   `--no-write-lock-file`. These commands continue to use the raw flake path,
   preserving the existing Git-indexed source semantics. No root fallback is
   attempted if the user's Nix setup cannot perform these operations.
4. Python runs `nvd diff`, prints the result, and obtains the existing
   yes/no confirmation. A declined upgrade, no-change result, build failure,
   or invalid flake never invokes `sudo` and never changes the system profile.
   The `nix flake show` pre-check is removed; the requested build is the
   authoritative validation and reports evaluation/build errors directly.
5. Only after a positive confirmation, Python invokes `/run/wrappers/bin/sudo`
   and the packaged privileged helper as a subprocess. GNU
   `env --ignore-signal` sets the supported signals to ignored dispositions before
   sudo starts. VM process inspection confirms sudo resets these dispositions
   for itself, while the helper and its switch descendants retain them; the
   activation flow does not rely on a signal mask surviving sudo. The command
   does not start a new session, so sudo retains the controlling terminal for
   its password prompt. The sudo prompt is left to host policy and occurs at
   this point, not during startup or build. No sudoers rule is installed
   automatically by the package or module.
6. The helper accepts one fixed activation/finalization request, not an
   arbitrary command. It validates the system closure and request data, creates
   or opens `/run/lock/nixos-upgrade-activation.lock` in the root-owned
   `/run/lock` directory, and takes a blocking `flock`. After acquiring it,
   the helper checks that the canonical current system still matches the
   closure Python observed before building; a waiting stale request is rejected
   without mutation. It then sets the system profile and runs that closure's
   `bin/switch-to-configuration switch` using fixed executable paths and
   argument vectors.
7. Only after a successful switch does the helper publish the updated lock
   file, if one was requested, and perform auto-commit as the Git repository
   owner. Lock publication runs as the resolved flake directory's owner and
   atomically replaces `flake.lock` without following a destination symlink.
   Commits continue to include modified tracked files, avoid empty commits, use
   the repository owner's Git identity, and honor `--no-commit`. This keeps the
   default root-owned `/etc/nixos` workflow working even though Python itself is
   unprivileged. The helper creates and cleans up only its own temporary files;
   it never receives or removes a caller-selected temporary path.
8. Python reports the helper's result. If activation succeeds but publishing
   the lock file or committing fails, report that as a post-activation
   failure/warning without claiming that activation failed or attempting an
   unsafe rollback. A cancelled or denied sudo request performs no activation.

The temporary lock contents and commit-message text cross the privilege
boundary as data only: Python sends a strict JSON object on helper stdin with
exactly `lock_file_base64` and `commit_message_base64` fields, each a base64
string or null. The lock field is null when no lock update is requested; the
commit-message field is null when `--no-commit` is selected. The helper rejects
unknown keys, wrong field types, and invalid base64 before mutation. No
caller-owned handoff directory or temporary path crosses the boundary, which
avoids root reopening files the invoking user could race. The helper must
never evaluate user-supplied shell text or accept an arbitrary command.

## Privilege and process boundaries

- The long-lived controller and all UI, CLI parsing, flake resolution,
  evaluation/build, diff, and normal Git inspection run with the invoking
  user's credentials.
- The only new root process is a one-shot packaged helper started by `sudo`
  after confirmation. It owns system-profile mutation and activation, plus the
  minimum lock-publication/owner-commit work needed to preserve behavior for
  root-owned flake repositories.
- The helper uses fixed NixOS operations and validates all paths and inputs.
  It does not run Nix evaluation/build, parse CLI options, or execute an
  arbitrary command received from Python.
- Sudo authorization remains host policy. A user permitted to activate a
  user-selected NixOS toplevel is trusted to administer that host; the project
  will not silently grant that authority through a NixOS module option.
- Keep the existing supported signal list and synchronous signal handling
  policy. During ordinary Nix build subprocesses, the controller retains its
  current separate-session cancellation and wait behavior. During privileged
  activation, GNU `env --ignore-signal` sets supported signals to ignored
  dispositions before sudo; VM inspection confirms the helper and its switch
  descendants retain them even though sudo resets its own dispositions. Python
  does not detach sudo from its controlling terminal and defers its own signal
  handling until the helper has exited and its result has been reported, so it
  neither orphans the helper nor claims success before completion.
- Preserve the per-user singleton lock. The root helper additionally
  serializes system activation across users and rejects a stale build if the
  current system changed after Python began the upgrade.

## Scope boundaries

- Replace the persistent root worker and line protocol with direct Python
  subprocess calls and a one-shot sudo helper.
- Run Nix lock update/build and `nvd diff` as the invoking user; run auto-commit
  as the repository owner through the final helper transaction when necessary.
- Remove the redundant `nix flake show` pre-check because the build reports the
  relevant errors directly.
- Preserve the current signal policy, CLI semantics, colors/logging, spinner,
  package outputs, and B.2 Git source semantics unless a narrowly necessary
  adjustment is identified and approved.
- Do not undertake the unrelated broad `CliProgram` module split or spinner
  redesign as part of this change.
- Do not add automatic sudoers, polkit, systemd service, or setuid-wrapper
  configuration.

## Validation

- Replace worker-protocol tests with tests for the user-side Nix command
  arguments, Git-indexed flake path, stdin JSON manifest, and no-sudo paths
  (decline, no changes, build failure).
- Use a fake sudo/helper in user-side tests to verify the helper is invoked
  only after confirmation, that helper exit statuses are handled, and that
  lock/commit options are passed without shell interpretation.
- Test the one-shot helper with controlled command stubs for profile update,
  switch ordering, stale-current-system rejection, lock publication, commit as
  repository owner, `--no-commit`, and post-activation failure reporting.
- Add or adapt a NixOS VM test using a normal test user with test-only sudo
  authorization. Verify successful activation and owner-based auto-commit in
  a root-owned `/etc/nixos` fixture, that sudo is not requested before
  confirmation, and that denied/cancelled authorization does not change the
  system profile.
- Run the existing CLI, logging, signal, source-semantics, auto-commit, shell
  and Python checks, plus `nix flake check`.
