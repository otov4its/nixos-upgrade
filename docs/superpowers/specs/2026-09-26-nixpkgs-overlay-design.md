# Nixpkgs package and overlay design

## Goal

Make `nixos-upgrade` reusable as a callPackage-style package, expose it as
`overlays.default`, and have the NixOS module default to the overlay package
provided by the consuming system's `pkgs`. Keep the standalone flake outputs
and development environment based on this project's pinned Nixpkgs input.

## Current behavior

`flake.nix` currently defines the full derivation inline, after importing this
flake's `nixpkgs` input for each system. The NixOS module sets
`programs.nixos-upgrade.package` to `self.packages.${system}.default`.
Consequently, a consuming NixOS configuration installs the package built from
this flake's Nixpkgs revision, rather than building it against the host's
`pkgs`. The README suggests `nixpkgs.follows` as an optional way to align the
Nixpkgs inputs.

## Proposed structure

### Shared package expression

Extract the derivation into a root-level `package.nix` function. Its build and
runtime dependencies are function arguments supplied by `callPackage`; the
package expression retains the current source layout, wrapper substitutions,
man-page generation, install checks, runtime tools, version, and production
Python options. This file is the single source of package construction logic.

### Shared overlay, applied to two package sets

Define one overlay function that adds `nixos-upgrade` by calling
`final.callPackage ./package.nix {}` and expose it as `overlays.default`.

Apply that same overlay to this flake's pinned Nixpkgs import. Define
`packages.default` from the resulting `pkgs.nixos-upgrade`; preserve `packages.dev`
as the development-options variant and keep the existing package alias.

When `programs.nixos-upgrade.enable` is true, the NixOS module adds
`overlays.default` to the consuming configuration's `nixpkgs.overlays`. The
module's package option defaults to `pkgs.nixos-upgrade`. Thus the package
definition is shared, but dependencies come from different package sets:

- Standalone outputs (`packages.default`, `packages.dev`, `nix develop`, and
  `nix run`) use this flake's pinned Nixpkgs input.
- The enabled NixOS module's default package uses the host configuration's
  Nixpkgs package set, including its overlays and package configuration.

Avoid defining the overlay in terms of `self.packages`; both the overlay and
pinned standalone outputs should use the shared package expression directly,
without a recursive dependency between flake outputs.

### NixOS module and compatibility override

Enabling the NixOS module registers the overlay so that its `package` option
can default to `pkgs.nixos-upgrade`. The overlay is not added when the program
is disabled. `programs.nixos-upgrade.package` remains user-overridable,
including selecting this flake's pinned package explicitly:

```nix
programs.nixos-upgrade.package =
  inputs.nixos-upgrade.packages.${pkgs.stdenv.hostPlatform.system}.default;
```

Building through host `pkgs` means the host's dependency versions are used.
In particular, the host's `nix` command must support the CLI options used by
`nixos-upgrade`; this design does not promise that every Nixpkgs revision is
compatible. Users can select the pinned package if their host package versions
are unsuitable.

If a NixOS configuration supplies an already-instantiated `nixpkgs.pkgs`, its
package set may not incorporate the module's `nixpkgs.overlays` option. Such a
configuration must apply `inputs.nixos-upgrade.overlays.default` when creating
that package set, or explicitly set `programs.nixos-upgrade.package` to a
suitable package.

### Flake input alignment and documentation

The project's `nixpkgs` input remains necessary for its standalone package and
development outputs. `inputs.nixos-upgrade.inputs.nixpkgs.follows = "nixpkgs"`
remains optional: it is not required for the module's host-`pkgs` package, but
it can make the standalone package use the consumer's Nixpkgs revision and
avoid a separate Nixpkgs node in the consumer's flake lock graph.

Update README installation examples to describe automatic overlay registration
by the module, host-package version behavior, the pinned-package override, and
the optional meaning of `follows`.

## Scope boundaries

- Keep the existing `nixosModules.default` and
  `programs.nixos-upgrade.{enable,package}` interfaces.
- Preserve the `nix run`, `nix shell`, and `nix profile` package outputs.
- Do not change runtime behavior or dependency requirements as part of this
  packaging refactor.
- Do not change the supported-system list or address other B.8 cleanup items.

## Validation

- Evaluate and build the pinned standalone `default` and `dev` outputs.
- Evaluate the overlay against a Nixpkgs set and confirm it provides the
  expected package.
- Evaluate a NixOS configuration importing the module and verify its default
  package resolves to the host `pkgs.nixos-upgrade` overlay derivation.
- Verify the package option can be overridden with the pinned standalone
  package.
- Run `nix flake check --no-build`, shell checks, and existing regression
  tests.
