# Sudo activation privilege separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the upgrade controller as the invoking user and elevate only a one-shot activation/finalization helper through `sudo`, after the user confirms the proposed upgrade.

**Architecture:** Python performs flake resolution, lock update, build, diff, and confirmation as the caller. After confirmation it invokes one packaged Bash helper through the NixOS sudo wrapper. The helper applies the system closure, then publishes the lock file and commits as the repository owner. The old long-lived worker and line protocol are removed.

**Tech Stack:** Python 3 standard library (`subprocess`, `tempfile`, `json`, `base64`, `unittest`); Bash; `jq`; Nixpkgs 26.05; NixOS VM tests; `sudo`, `runuser`, and `flock`.

**Spec:** `docs/superpowers/specs/2026-09-27-user-controller-sudo-activation-design.md`

## Global Constraints

- Launch the app normally as the invoking user; never run the controller as root or drop it to `nobody`.
- Request sudo only after a positive upgrade confirmation; `--assume-yes` never bypasses sudo authorization.
- Run flake update, Nix build, and `nvd diff` as the invoking user; do not fall back to root if they fail.
- Preserve raw Git-flake source semantics: newly created files must be indexed by Git before Nix can use them; stage only the new files needed by a Nix check, and do not commit unless explicitly requested.
- Preserve the default `/etc/nixos` path, `--no-update-lock-file`, `--no-commit`, `--assume-yes`, `--assume-no`, and repository-owner auto-commit policy.
- Do not install sudoers rules, polkit actions, systemd services, or setuid wrappers automatically.
- Preserve the existing supported signal list and synchronous signal handling policy.
- Keep the existing per-user singleton lock; serialize system activation across users and reject stale builds.
- Do not broadly split `CliProgram` or redesign colors/spinner as part of this change.
- Do not commit unless explicitly requested.

## Review Focus

- **Declined, denied, or noninteractive sudo request:** no system-profile change, lock publication, or commit; test the no-sudo/decline paths in the user workflow and denied/noninteractive sudo in the NixOS VM test.
- **Root-owned `/etc/nixos` and owner-based Git commit:** publish lock and commit only after a successful switch, as the repository owner; test in the VM fixture.
- **Malformed or raced activation input / unsafe destination:** accept only the strict JSON data manifest on stdin, never a caller-provided handoff-file path; atomically publish `flake.lock` as the flake directory owner without following a destination symlink; test malformed payloads and a symlinked lock destination in the VM.
- **Concurrent upgrades from different users:** hold the root-controlled activation lock and reject a build whose expected current closure is stale; test competing/stale requests in the VM.
- **Signals during build or privileged activation:** during a build terminate and reap its separate process group; during sudo/helper activation, set supported signals to ignored dispositions for the helper and its descendants, let the transaction finish, report its actual result, then process Python's queued signal; test both phases. The VM launcher resets inherited SIGINT to default to model a foreground terminal invocation.

---

## File map

| File                                                                              | Responsibility after the change                                                                                                                                                                                                                    |
| --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/bin/nixos-upgrade`                                                           | Unprivileged environment/lock setup, supported-signal blocking, help/version compatibility, then `exec` Python; no worker startup or privilege drop.                                                                                               |
| `src/lib/nixos-upgrade.py`                                                        | User-side workflow, temporary lock workspace, diff/confirmation, and direct helper invocation.                                                                                                                                                     |
| `src/lib/activation.py`                                                           | Build the safe sudo/helper argv, encode the data-only JSON stdin manifest, and validate the helper's one-shot JSON result; package substitution sets the helper path, while `shutil.which("env")` resolves GNU env from the launcher's fixed PATH. |
| `src/lib/nixos-upgrade-activate`                                                  | One-shot root Bash helper for profile update, system switch, lock publication, and owner-based commit; it reads content, never handoff paths, from stdin.                                                                                          |
| `package.nix`                                                                     | Substitute the helper path and fixed command paths, add `jq`; run shell/Python checks; stop installing the persistent worker.                                                                                                                      |
| `flake.nix`                                                                       | Add Python unit-test and x86_64-linux NixOS VM checks with test-only sudo policy.                                                                                                                                                                  |
| `tests/test_activation.py`                                                        | Unit tests for command construction and helper-result parsing.                                                                                                                                                                                     |
| `tests/sudo-activation-vm.nix`                                                    | Exercise helper and full user-to-sudo workflow in a disposable NixOS VM.                                                                                                                                                                           |
| Existing `tests/*.sh`                                                             | Replace worker-protocol assumptions and retain the current CLI, logging, signal, lock, and commit regressions.                                                                                                                                     |
| `README.md`, `src/share/man/man8/nixos-upgrade.8.md`, `CHANGELOG.md`, `REVIEW.md` | Document invocation/elevation timing, record the change, and mark B.1 chosen/implemented.                                                                                                                                                          |

## Interfaces

### User-to-helper command and request data

`activation.build_activation_command(request: ActivationRequest) -> list[str]` returns an argv vector, never a shell string. The command shape is:

```text
<ENV> --ignore-signal=<comma-separated supported signal numbers>
  /run/wrappers/bin/sudo -- <HELPER> activate
  --expected-current <old-closure>
  --system-closure <new-closure>
  --flake-dir <resolved-flake-dir>
  [--no-commit]
```

Python sends a strict JSON object on stdin with exactly `lock_file_base64` and `commit_message_base64` fields. Each field is either a base64 string or null: the lock is null when there is no lock update to publish, and the commit message is null when `--no-commit` is set. The JSON carries file contents only, never a filesystem path. The helper uses `jq` to validate the exact schema and GNU `base64 --decode` to validate/decode the content; it creates any temporary files itself and never recursively removes a caller-provided path.

Do not run this sudo child in a new session: sudo must retain the controlling terminal for its password prompt. Its stderr remains attached to the terminal, stdout carries only the helper result, and stdin carries the JSON manifest. The command is prefixed by GNU `env --ignore-signal=...`, setting supported signal dispositions to ignored before sudo starts. VM process inspection confirms sudo resets these dispositions for itself, while the helper and its switch descendants retain them; the activation flow therefore does not rely on a signal mask surviving sudo. Python wraps the one-shot subprocess and result reporting in `synsignals.BlockedHandling`, deferring its own queued signal handling until the helper has exited and the actual activation result has been reported. Ordinary build subprocesses retain their existing separate-session termination behavior.

### Helper result

On execution, the helper writes one JSON object to stdout and diagnostics/command output to stderr:

```json
{
  "system": "switched|invalid-request|stale|profile-failed|switch-failed",
  "lock": "published|not-requested|failed|not-run",
  "commit": "committed|no-changes|not-git|not-requested|failed|not-run"
}
```

Exit status is zero only when the system switch succeeded. A post-switch lock/commit failure is represented in the JSON while preserving `system: "switched"`; Python reports the secondary failure without saying activation failed. Invalid requests, stale closures, and activation failures return nonzero with a JSON result when the helper started. A sudo authorization failure may produce no JSON, which Python reports as a failed/cancelled sudo request. The parser rejects unknown statuses and inconsistent JSON/exit-status combinations.

### Python subprocess result

Change `CliProgram.run_cmd(...)` to return `subprocess.CompletedProcess[str]`, retaining current logging, spinner, signal polling, and default error behavior. Construct it with the command, return code, collected stdout, and `stderr=None` (stderr is forwarded as today). `diff_closures()` uses `.stdout`. The activation-specific runner uses `subprocess.run(input=..., stdout=PIPE, stderr=None, text=True, check=False)` without `start_new_session`, and parses `.stdout` with `.returncode` even on nonzero helper status.

## Tasks

### Task 1: Pin the sudo/helper interface with unit tests

**Files:**

- Create: `src/lib/activation.py`
- Create: `tests/test_activation.py`
- Modify: `docs/superpowers/specs/2026-09-27-user-controller-sudo-activation-design.md`
- Modify: `flake.nix`

**Interfaces:**

- Produces `ActivationRequest`, `ActivationResult`, `build_activation_command(request) -> list[str]`, `encode_activation_request(request) -> str`, and `parse_activation_result(stdout, returncode) -> ActivationResult`.
- `ActivationRequest` fields: `env_path: str`, `supported_signals: tuple[int, ...]`, `sudo_path: str`, `helper_path: str`, `expected_current: str`, `system_closure: str`, `flake_dir: str`, `lock_file_bytes: bytes | None`, `commit_message: str | None`, and `no_commit: bool`. Production `sudo_path` is `/run/wrappers/bin/sudo`. `encode_activation_request()` base64-encodes lock bytes and UTF-8 commit-message bytes into exactly the two documented JSON fields; `no_commit=True` requires a null commit message, and `no_commit=False` requires a string.
- `ActivationResult` fields: `system`, `lock`, and `commit`, restricted to the values in the helper-result contract above; the parser verifies that `system == "switched"` corresponds to exit status zero and all other system statuses to nonzero.

- [ ] **Step 1: Align the approved spec with the selected transport and signal behavior**
  - Replace the user-owned path handoff requirements with the strict base64 JSON stdin manifest; document that the helper receives no caller-owned temporary paths.
  - Specify atomic lock publication as the flake directory owner, the retained sudo controlling TTY, and signal blocking/deferred reporting during activation.
- [ ] **Step 2: Write failing unit tests**
  - `test_activation_command_keeps_each_path_as_one_argument`: use paths containing spaces and shell metacharacters; assert they remain distinct argv elements, the env signal-ignore prefix is correct, sudo is the NixOS wrapper, and no shell is requested.
  - `test_activation_command_adds_no_commit_only_when_requested`: assert both `no_commit` values.
  - `test_encode_activation_request_carries_only_base64_contents`: preserve binary lock bytes and multiline commit text; assert no caller-selected handoff path appears in the payload.
  - `test_parse_activation_result_accepts_each_documented_status`: assert all documented system/lock/commit statuses parse with consistent exit codes.
  - `test_parse_activation_result_rejects_malformed_or_unknown_status`: malformed JSON, missing fields, unknown values, and inconsistent system/exit status raise `ValueError`.
- [ ] **Step 3: Run the tests and confirm the expected failures**

  Run: `python3 -m unittest discover -s tests -p 'test_activation.py' -v`

  Expected: FAIL because the activation interface does not exist yet.

- [ ] **Step 4: Implement the typed request/result and pure command/result functions**

  Keep subprocess execution out of this module; it only encodes argv/manifest data and validates the fixed JSON result. Add a `checks.x86_64-linux.python-unit-tests` check that runs `python3 -m unittest discover -s tests -p 'test_*.py' -v`.

- [ ] **Step 5: Index new files and run the focused tests/check**

  Run: `git add src/lib/activation.py tests/test_activation.py && python3 -m unittest discover -s tests -p 'test_activation.py' -v && nix build .#checks.x86_64-linux.python-unit-tests`

  Expected: focused unit tests and the Nix check pass.

### Task 2: Add the one-shot privileged helper and VM contract test

**Files:**

- Create: `src/lib/nixos-upgrade-activate`
- Create: `tests/sudo-activation-vm.nix`
- Modify: `package.nix`
- Modify: `flake.nix`

**Interfaces:**

- The helper implements the `activate` argv and stdin-manifest contract in Task 1 and emits the documented JSON result.
- The helper uses fixed NixOS paths for `/nix/var/nix/profiles/system` and `/run/current-system`; it starts with `PATH=@path@` substituted to the package's fixed runtime path, including `jq` and `base64`.
- It creates/opens `/run/lock/nixos-upgrade-activation.lock` at runtime in the root-owned `/run/lock` directory, verifies the lock path is a root-owned regular file (not a symlink), and takes a blocking `flock`; it never unlinks the lock. After obtaining it, it compares canonical `/run/current-system` with `--expected-current`, so a waiting second request is rejected as stale.
- Lock publication runs as the numeric owner of the resolved flake directory, writes a same-directory temporary file, and atomically renames it over `flake.lock` without following a destination symlink. Git commit runs as the numeric owner of the absolute Git directory, with that owner's home directory.
- The VM test uses a test-only sudo rule for the exact helper path; no production sudoers rule is added.

- [ ] **Step 1: Add a failing NixOS VM test for the helper transaction**
  - Configure one normal test user with test-only permission to invoke only the helper and a second normal user without that permission for denied/noninteractive sudo coverage.
  - Use a root-owned Git flake fixture and a fake system closure whose `switch-to-configuration` records invocation.
  - Assert a valid request sets the profile, switches, publishes an optional lock after the switch, and commits tracked changes with the Git-directory owner's UID, home directory, and Git identity.
  - Assert `--no-commit` skips Git commit; an unchanged repository creates no empty commit.
  - Assert stale-current, invalid closure, malformed/extra-field/invalid-base64 stdin manifests, and failed-profile/failed-switch cases do not publish/commit; for a symlinked `flake.lock` destination, assert atomic replacement leaves the symlink target untouched.
  - Start two requests with the same expected closure: hold the first inside its fake switch, verify the second blocks on the root activation lock, then let the first update `/run/current-system` and assert the second returns `stale` without changing the profile.
- [ ] **Step 2: Wire the VM test into `checks.x86_64-linux` and run it to confirm failure**

  Run: `git add tests/sudo-activation-vm.nix && nix build .#checks.x86_64-linux.sudo-activation`

  Expected: FAIL because the helper output and behavior are not implemented.

- [ ] **Step 3: Implement the Bash helper**
  - Require effective UID 0 and exact `activate` arguments; reject extras and malformed paths. Parse exactly the JSON stdin schema with `jq`; reject unknown keys, wrong field types, and invalid base64 before mutation.
  - Create/open `/run/lock/nixos-upgrade-activation.lock` at runtime in the root-owned `/run/lock` directory; reject a symlink, non-regular, or non-root-owned lock file, take a blocking `flock`, and never unlink it. After acquiring it, compare the canonical `/run/current-system` with `--expected-current` before mutation, returning `stale` if another upgrade already changed the system.
  - Validate that the target closure is a canonical `/nix/store` path with an executable `bin/switch-to-configuration`; validate the resolved flake directory and its `flake.nix` before repository operations.
  - Set `/nix/var/nix/profiles/system`, then run `switch-to-configuration switch`.
  - Only after a successful switch, if lock bytes were requested, use the numeric owner of the resolved flake directory to create/write a same-directory temporary file and atomically rename it over `flake.lock`; do not follow a destination symlink. Determine the Git commit owner from the absolute Git directory's numeric owner, set `HOME` from that account, and run `git commit --all --file=-` as that owner; preserve no-empty-commit and owner-identity behavior.
  - Never evaluate shell text from arguments or manifest data and never use root to write to a caller-selected repository on behalf of a non-root owner. Emit one JSON status object; send command output/diagnostics to stderr.
- [ ] **Step 4: Add the helper to the derivation and run its static checks**
  - Substitute `@helper@` in `activation.py` with `$out/lib/nixos-upgrade-activate`, substitute `@path@` in the helper with the runtime path, and add `jq` to runtime inputs.
  - Run ShellCheck on the helper and ensure it is executable in the package output.

  Run: `git add src/lib/nixos-upgrade-activate && nix build .#packages.x86_64-linux.default && nix build .#checks.x86_64-linux.sudo-activation`

  Expected: package/install checks and the VM helper test pass.

### Task 3: Replace the persistent worker with the unprivileged controller and sudo workflow

**Files:**

- Modify: `src/lib/nixos-upgrade.py`
- Modify: `src/lib/activation.py`
- Modify: `src/bin/nixos-upgrade`
- Delete: `src/lib/privileged-worker`
- Modify: `package.nix`
- Create or modify: `tests/test-user-workflow.sh`
- Modify: `tests/test-git-flake-source.sh`
- Modify: `tests/test-cli-options.sh`
- Modify: `tests/test-auto-commit-policy.sh`
- Modify: `tests/test-substitute-placeholders.sh`
- Keep/verify: `tests/test-singleton-lock.sh`, `tests/test-signal-list.sh`, `tests/test-preserve-handler.sh`

**Interfaces:**

- `CliProgram.run_cmd(...) -> subprocess.CompletedProcess[str]` retains output forwarding and returns both `returncode` and `stdout`.
- Add `CliProgram.run_privileged_activation() -> ActivationResult`; call it only in the positive-confirmation branch. It passes the JSON manifest via subprocess stdin, keeps sudo's stderr/controlling TTY available, sets supported signal dispositions to ignored for the helper chain, and reports the parsed activation result before processing any queued signal.
- `CliProgram` owns a temporary work directory for the Nix lock/build outputs. It reads lock bytes and constructs the commit message as data for the stdin manifest; the helper receives no caller-owned temporary paths and performs no cleanup of them.
- The launcher remains unprivileged, keeps the per-user singleton lock and supported-signal blocking, and execs Python directly. The worker, IPC descriptors, and `setpriv` drop are removed as part of the same integrated change so no intermediate state ships with mismatched Python/launcher protocols.

- [ ] **Step 1: Add failing workflow and process-boundary tests**
  - Assert the raw flake path is used for `nix flake update` and `nix build`, each with `--extra-experimental-features "nix-command flakes"`; also assert `--output-lock-file`, `--reference-lock-file` when available, and `--no-write-lock-file` on build.
  - Assert the temporary copy of an existing lock is used when `--no-update-lock-file` is selected and that no lock bytes are sent for publication.
  - Assert `nix flake show` is not invoked; build failures are surfaced directly.
  - Assert decline, identical closures, and build failure do not call `run_privileged_activation`; yes calls it once after confirmation, and `--assume-yes` still calls it.
  - Assert helper JSON with `system: switched` plus failed lock/commit status reports successful activation and a distinct post-activation error; assert the stdin manifest is separate from argv and preserves multiline commit data.
  - Assert the launcher has no worker, `setpriv` to `nobody`, IPC FDs, or pipe protocol; it retains the per-user lock, fixed PATH, help/version/color behavior, and signal blocking. Update CLI, auto-commit, and placeholder tests for the helper.
- [ ] **Step 2: Run the focused tests and confirm expected failures**

  Run: `bash tests/test-user-workflow.sh && bash tests/test-git-flake-source.sh && bash tests/test-cli-options.sh && bash tests/test-auto-commit-policy.sh && bash tests/test-singleton-lock.sh && bash tests/test-signal-list.sh`

  Expected: the user-workflow and process-boundary assertions fail against the current worker-based design.

- [ ] **Step 3: Implement the integrated user workflow and launcher transition**
  - Read `/run/current-system` directly; resolve/check the flake path in Python without changing raw Git-path semantics. Remove Python's worker-FD setup, `run_privileged_task`, and line-protocol read/write helpers.
  - Keep the Nix temporary workspace owned by the invoking user; use it for existing/generated lock files, then encode requested lock bytes and commit-message text into the JSON stdin manifest. Never pass the helper a user-owned temporary path.
  - Run `nix flake update`, `nix build`, and `nvd diff` as the invoking user. Remove `check_nixos_config`'s `nix flake show` pre-check.
  - Update `run_cmd` to return `CompletedProcess[str]`; update the NVD caller to use `.stdout`.
  - Construct the command and JSON stdin payload via `activation.py`; invoke `/run/wrappers/bin/sudo` without `start_new_session` so it retains the controlling terminal for authentication. Keep stderr attached, capture only helper JSON on stdout, and run under `synsignals.BlockedHandling` until the result has been parsed and reported.
  - Remove root-only setup, worker startup, IPC descriptors, and Python `setpriv`; exec Python as the invoking user while preserving the fixed PATH, per-user lock, supported-signal blocking, CLI output behavior, and singleton-lock inheritance. Delete the persistent worker and remove its package substitutions/install checks; retain helper substitutions/checks.
- [ ] **Step 4: Run the integrated regressions and package build**

  Run: `bash tests/test-user-workflow.sh && bash tests/test-git-flake-source.sh && bash tests/test-cli-options.sh && bash tests/test-auto-commit-policy.sh && bash tests/test-singleton-lock.sh && bash tests/test-signal-list.sh && bash tests/test-preserve-handler.sh && bash tests/test-substitute-placeholders.sh && git add -u src/lib/privileged-worker && nix build .#packages.x86_64-linux.default`

  Expected: the app runs unprivileged, tests pass, and the package includes only the one-shot privileged helper.

### Task 4: Exercise the complete app in the VM and update user-facing documentation

**Files:**

- Modify: `tests/sudo-activation-vm.nix`
- Modify: `flake.nix`
- Modify: `README.md`
- Modify: `src/share/man/man8/nixos-upgrade.8.md`
- Modify: `CHANGELOG.md`
- Modify: `REVIEW.md`

- [ ] **Step 1: Extend the VM test to run the app as a normal user**
  - Run the packaged `nixos-upgrade` as the test user against root-owned `/etc/nixos`.
  - Assert preparation/build/diff happen before the sudo helper; a negative answer does not invoke sudo, and denied/noninteractive sudo does not mutate the profile or repository.
  - Assert a confirmed run activates, publishes the new lock after switch success, and commits as the repository owner; verify `--no-commit` and `--no-update-lock-file` behavior and malformed stdin rejection.
  - Deliver a supported signal during build and a terminal-generated signal to a dedicated shared process group after the helper has started; assert build cancellation reaps its separate process group, the helper/switch chain ignores SIGINT/SIGTERM while the controller catches and defers SIGINT, then waits for completion and reports the actual activation result before exiting. Reset the VM background launcher's inherited SIGINT disposition to default so the test models a foreground terminal invocation.
- [ ] **Step 2: Run the end-to-end VM test**

  Run: `nix build .#checks.x86_64-linux.sudo-activation`

  Expected: the normal-user workflow succeeds with test-only sudo authorization; no production sudoers configuration is involved.

- [ ] **Step 3: Document invocation, elevation, and the selected transport**
  - README and man page state to run the app without `sudo`; sudo authorization is requested only after upgrade confirmation.
  - State that `--assume-yes` does not bypass sudo and that the user must have host-configured sudo authorization.
  - Add an Unreleased changelog entry and update the B.1/order-of-work section in `REVIEW.md`.
- [ ] **Step 4: Run the complete regression and package checks**

  Run: `python3 -m unittest discover -s tests -p 'test_*.py' -v && for test in tests/*.sh; do bash "$test"; done && nix flake check`

  Expected: all shell tests and flake checks pass. Record any existing Nix warnings separately; do not broaden this task to fix unrelated warnings.
