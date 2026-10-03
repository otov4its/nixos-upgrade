# ADR 0001: Keep the controller unprivileged and elevate for activation

- Status: Accepted
- Date: 2026-10-03

## Context

The tool must update the system profile and run `switch-to-configuration`, which
require root. Before activation, it also evaluates and builds a user-selected
flake, inspects repository state, displays a diff, and asks for confirmation.
Running that larger workflow as root would unnecessarily expand the privileged
code path. Some supported configurations, including the default `/etc/nixos`,
are root-owned, so post-activation lock publication or Git commits may need to
run as the repository owner.

## Decision

Run the launcher and Python controller as the invoking user. Keep flake
resolution, lock update, build, diff, and confirmation unprivileged. Only after
confirmation invoke one fixed-purpose activation helper through `sudo`. Pass a
validated data request to the helper; do not allow it to execute arbitrary
commands or shell text. The helper switches the system and performs requested
post-switch lock publication and repository-owner commit work. Sudo policy
remains the host administrator's responsibility.

## Consequences

- A decline, no-change result, or preparation failure does not invoke `sudo` or
  mutate the system profile.
- The sudo prompt occurs only when activation is requested. `--assume-yes` does
  not bypass host authorization.
- The controller/helper boundary adds protocol validation and coordination
  complexity; tests must cover the helper request, activation order, and failure
  reporting.
- A successful system switch is not rolled back if later lock publication or
  commit work fails; report those post-activation failures separately.

## Alternatives considered

- Run the whole workflow as root and drop privileges only for the UI. Rejected
  because Nix evaluation/build and more application logic would remain in the
  privileged process.
- Configure sudoers or another host authorization policy from the package or
  NixOS module. Rejected because authorization is host policy, not an automatic
  application side effect.
