# ADR 0003: Use host packages for the module and pinned packages standalone

- Status: Accepted
- Date: 2026-10-03

## Context

A NixOS module can install a package built from this flake's pinned Nixpkgs or
from the consuming system's package set. Always selecting the pinned package
provides a known dependency set, but can add another Nixpkgs closure to a host
that already has its own package set and overlays. Conversely, host packages
may have versions that do not support the Nix CLI options used by the tool.

## Decision

Use one `package.nix` expression through `overlays.default`. The enabled NixOS
module adds that overlay and defaults to `pkgs.nixos-upgrade`, using the host's
package set. Standalone flake outputs, including the development package, remain
built against the flake's pinned Nixpkgs. Keep the module's package option
available so users can explicitly select the pinned standalone package when
their host package versions are unsuitable.

## Consequences

- The module uses host package configuration by default and does not require a
  second Nixpkgs package set solely for this tool.
- The host's `nix` command must support the CLI options used by
  `nixos-upgrade`; compatibility with every host Nixpkgs revision is not
  guaranteed.
- Users can select the pinned package explicitly. Configurations that provide
  an already-instantiated `nixpkgs.pkgs` must apply the overlay when creating
  that package set or choose an explicit package.
- Standalone builds and development remain reproducible against the flake's
  pinned Nixpkgs input.

## Alternatives considered

- Make the NixOS module always use the pinned standalone package. Rejected as
  the default because it may introduce a second Nixpkgs closure and bypass the
  host's package configuration. The pinned package remains an explicit
  compatibility option.
