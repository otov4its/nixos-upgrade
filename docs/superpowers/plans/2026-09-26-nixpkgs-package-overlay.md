# Nixpkgs Package Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `nixos-upgrade` into a reusable callPackage package, expose `overlays.default`, and make the enabled NixOS module use the host's overlaid package set while preserving pinned standalone outputs.

**Architecture:** Move the existing derivation into `package.nix`, parameterizing its build/runtime dependencies and Python options. Define one overlay that calls that expression; apply it to this flake's pinned Nixpkgs for standalone outputs and to the host's Nixpkgs from the enabled NixOS module. Preserve the module package override so users can select the pinned standalone derivation.

**Tech Stack:** Nix flakes, Nixpkgs `callPackage` and overlays, NixOS modules, Bash regression tests.

**Spec:** `docs/superpowers/specs/2026-09-26-nixpkgs-overlay-design.md`

## Global Constraints

- Standalone outputs (`packages.default`, `packages.dev`, `nix develop`, and `nix run`) use this flake's pinned Nixpkgs input.
- The enabled NixOS module's default package uses the host configuration's Nixpkgs package set, including its overlays and package configuration.
- The host's `nix` command must support the CLI options used by `nixos-upgrade`; this design does not promise that every Nixpkgs revision is compatible.
- Users can select the pinned package if their host package versions are unsuitable.
- Keep the existing `nixosModules.default` and `programs.nixos-upgrade.{enable,package}` interfaces.
- Do not change runtime behavior or dependency requirements as part of this packaging refactor.
- Do not change the supported-system list or address other B.8 cleanup items.

## Review Focus

- **Enabled module selects host package, not pinned package:** pin with an NixOS evaluation check comparing the configured package's `drvPath` to `nixosSystem.pkgs.nixos-upgrade.drvPath` (Task 2).
- **Disabled module does not inject the overlay:** compare the disabled module's package set with an unextended import of the same Nixpkgs revision, checking both attribute presence and derivation identity when that package exists (Task 2).
- **Pinned package override remains effective:** set `programs.nixos-upgrade.package` to `self.packages.x86_64-linux.default` and assert the configured `drvPath` matches it (Task 2).
- **`package = null` still opts out of installation:** assert that the host overlay package is absent from `environment.systemPackages` (Task 2).
- **Already-instantiated `nixpkgs.pkgs` does not receive the module overlay retroactively:** exercise the documented supported setup by constructing that `pkgs` with `overlays.default` already applied, then verify the module uses that package (Task 2); document the requirement in README (Task 3).

---

### Task 1: Extract the package expression and publish the shared overlay

**Files:**
- Create: `package.nix`
- Modify: `flake.nix`

**Interfaces:**
- Produces `overlays.default`, an overlay that adds `nixos-upgrade` using `final.callPackage ./package.nix {}`.
- Produces `packages.${system}.default`, `.dev`, and `.nixos-upgrade`; standalone package outputs use this flake's pinned Nixpkgs.

- [ ] **Step 1: Add a failing overlay-output check**

Add the `checks` field to the attrset returned by the existing `eachSystem` function, alongside `packages` and `devShells`. The helper transposes every callback field under its system name, so this placement produces the standard `checks.<system>.<name>` shape; do not wrap another `eachSystem` around this field. Initially add this Linux-only overlay check before defining the overlay:

```nix
checks = if system == "x86_64-linux" then
  let
    basePkgs = import nixpkgs { inherit system; };
    overlayPkgs = basePkgs.extend self.overlays.default;
  in {
    package-overlay =
      assert overlayPkgs.nixos-upgrade.drvPath
        == self.packages.${system}.default.drvPath;
      basePkgs.runCommand "nixos-upgrade-overlay-check" { }
        "touch $out";
  }
else { };
```

The resulting flake output is top-level `checks.x86_64-linux.package-overlay`, alongside `packages` and `devShells`. Do not add the check under the NixOS module output.

- [ ] **Step 2: Run the check and confirm the expected failure**

Run: `nix flake check --no-build`
Expected: evaluation fails because `overlays.default` is not yet exported. This is the red phase proving the check exercises the missing overlay interface.

- [ ] **Step 3: Create `package.nix` from the current derivation**

Move the existing `mkDerivation` body from `flake.nix` into a root-level function. Give it this `callPackage` interface:

```nix
{ lib, stdenv, stdenvNoCC, python3, pandoc, git, nvd, nix, man, coreutils,
  glibc, util-linux, shellcheck,
  pyOpts ? "-B -s -OO -E -Wignore --check-hash-based-pycs never" }:
```

`stdenv` is required because the current install checks use `stdenv.shellDryRun`; use `lib.makeBinPath` for the runtime path. Build `pythonWithPkgs` from the injected `python3` with `yaspin` and `termcolor`; get `pyflakes`, `rope`, `yapf`, `mccabe`, and `pycodestyle` from `python3.pkgs`. Keep the current source path, runtime path construction, man-page generation, install checks, and runtime tools unchanged. Keep package identity and metadata equal to the current values: `pname = "nixos-upgrade"`, `version = "2026-07-01-1.0.5"`, and description `"NixOS upgrade showing what will be changed"`. Define these values in `package.nix` so its callPackage interface only needs the Nixpkgs dependencies and optional `pyOpts`.
Use `pyOpts` for the `@py_opts@` substitution. This lets the standalone dev output call the same package expression with `pyOpts = "-B -s"`.

- [ ] **Step 4: Define and export the overlay, then apply it to pinned outputs**

Define the overlay once in the flake's shared `let` scope:

```nix
nixosUpgradeOverlay = final: _prev: {
  nixos-upgrade = final.callPackage ./package.nix { };
};
```

Export it as `overlays.default = nixosUpgradeOverlay`. Import the flake's pinned `nixpkgs` with `overlays = [ nixosUpgradeOverlay ]`, set `packages.default = pkgs.nixos-upgrade`, preserve the `nixos-upgrade` alias, and define `packages.dev = pkgs.callPackage ./package.nix { pyOpts = "-B -s"; }`. Do not make the overlay refer to `self.packages`.

- [ ] **Step 5: Run the focused flake check and standalone builds**

Run: `nix flake check --no-build`
Expected: the `package-overlay` check evaluates successfully.

Run: `nix build .#default --no-link && nix build .#dev --no-link`
Expected: both outputs build successfully and retain the existing production/development wrapper options.

- [ ] **Step 6: Commit the package extraction and overlay output**

```bash
git add package.nix flake.nix
git commit -m "nixos-upgrade: expose reusable package overlay"
```

### Task 2: Make the enabled NixOS module use host `pkgs`

**Files:**
- Modify: `flake.nix`

**Interfaces:**
- Consumes `overlays.default` from Task 1.
- Produces a module default of `pkgs.nixos-upgrade` when `programs.nixos-upgrade.enable = true`.
- Preserves user overrides, including the pinned standalone package and `null`.

- [ ] **Step 1: Add NixOS module evaluation checks before changing the module**

Add the following helper and NixOS evaluations to the `let` block for `checks = if system == "x86_64-linux" then ...`, inside the existing `eachSystem` callback from Task 1. Place them after `basePkgs` and `overlayPkgs` but before `in`. Reuse its unextended `basePkgs` binding, and retain the existing `package-overlay` check in the returned attrset:

```nix
containsDrv = drvPath: packages:
  builtins.any (package: package.drvPath == drvPath) packages;

hostSystem = nixpkgs.lib.nixosSystem {
  inherit system;
  modules = [
    self.nixosModules.default
    { programs.nixos-upgrade.enable = true; }
  ];
};

pinnedSystem = nixpkgs.lib.nixosSystem {
  inherit system;
  modules = [
    self.nixosModules.default
    {
      programs.nixos-upgrade.enable = true;
      programs.nixos-upgrade.package = self.packages.${system}.default;
    }
  ];
};

nullPackageSystem = nixpkgs.lib.nixosSystem {
  inherit system;
  modules = [
    self.nixosModules.default
    {
      programs.nixos-upgrade.enable = true;
      programs.nixos-upgrade.package = null;
    }
  ];
};

disabledSystem = nixpkgs.lib.nixosSystem {
  inherit system;
  modules = [ self.nixosModules.default ];
};

explicitPkgs = import nixpkgs {
  inherit system;
  overlays = [ self.overlays.default ];
};

preInstantiatedPkgsSystem = nixpkgs.lib.nixosSystem {
  inherit system;
  modules = [
    self.nixosModules.default
    {
      nixpkgs.pkgs = explicitPkgs;
      programs.nixos-upgrade.enable = true;
    }
  ];
};
```

Add a `nixos-module-package-selection` value to the returned checks attrset, using these assertions before the `runCommand`:

```nix
nixos-module-package-selection =
  assert hostSystem.config.programs.nixos-upgrade.package.drvPath
    == hostSystem.pkgs.nixos-upgrade.drvPath;
  assert (containsDrv hostSystem.pkgs.nixos-upgrade.drvPath
    hostSystem.config.environment.systemPackages);
  assert pinnedSystem.config.programs.nixos-upgrade.package.drvPath
    == self.packages.${system}.default.drvPath;
  assert (containsDrv self.packages.${system}.default.drvPath
    pinnedSystem.config.environment.systemPackages);
  assert nullPackageSystem.config.programs.nixos-upgrade.package == null;
  assert !(containsDrv nullPackageSystem.pkgs.nixos-upgrade.drvPath
    nullPackageSystem.config.environment.systemPackages);
  assert (disabledSystem.pkgs ? nixos-upgrade) == (basePkgs ? nixos-upgrade);
  assert (!(basePkgs ? nixos-upgrade)
    || disabledSystem.pkgs.nixos-upgrade.drvPath == basePkgs.nixos-upgrade.drvPath);
  assert preInstantiatedPkgsSystem.config.programs.nixos-upgrade.package.drvPath
    == explicitPkgs.nixos-upgrade.drvPath;
  basePkgs.runCommand "nixos-upgrade-nixos-module-check" { } "touch $out";
```

The `disabledSystem` comparison verifies that the module adds no change to the base package set, without assuming upstream can never gain a package named `nixos-upgrade`. The pre-instantiated-`pkgs` test deliberately constructs `pkgs` with the overlay already applied, matching the documented supported setup. `nix flake check --no-build` evaluates these module assertions and the check derivation without building a complete NixOS system.

- [ ] **Step 2: Run the module check and verify it exposes the old default**

Run: `nix flake check --no-build`
Expected: the enabled module check fails because the current module selects `self.packages.${system}.default` and does not add the overlay to the host `pkgs` set.

- [ ] **Step 3: Wire the overlay and package default into the module**

In `nixosModules.${name}`, change the `package` option default from `self.packages.${system}.default` to `pkgs.nixos-upgrade` and remove the now-unused `system = pkgs.stdenv.hostPlatform.system;` binding. Under `config = lib.mkIf cfg.enable`, add:

```nix
nixpkgs.overlays = [ self.overlays.default ];
```

Keep this overlay addition inside the enabled configuration so importing the module without enabling the program does not alter the package set. Keep the existing `environment.systemPackages` logic so `package = null` remains an opt-out and explicit package values continue to override the default.

- [ ] **Step 4: Run NixOS module checks and confirm host/pinned/null behavior**

Run: `nix flake check --no-build`
Expected: the host package, pinned override, disabled-module, and null-package assertions all evaluate successfully.

- [ ] **Step 5: Commit the NixOS module integration**

```bash
git add flake.nix
git commit -m "nixos-upgrade: use host package set in NixOS module"
```

### Task 3: Document package-set selection and run full validation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Documents the existing NixOS module import/enable flow, optional `nixpkgs.follows`, and explicit pinned-package override.

- [ ] **Step 1: Update the NixOS installation example**

Retain the `nixos-upgrade` flake input and module import. Explain that enabling the module adds the overlay automatically and makes the module default use the host package set. Reword the `nixpkgs.follows` comment to state that it is optional, aligns standalone outputs with the host revision, and can deduplicate the lock graph; it is not required for the module's host package.

- [ ] **Step 2: Document compatibility and the pinned-package override**

Add this explicit override example to the NixOS section:

```nix
programs.nixos-upgrade.package =
  inputs.nixos-upgrade.packages.${pkgs.stdenv.hostPlatform.system}.default;
```

Explain that the host's `nix` package must support the CLI options used by the tool, and that users can select the pinned standalone output when host package versions are incompatible. Document that configurations supplying `nixpkgs.pkgs` directly must apply `inputs.nixos-upgrade.overlays.default` when creating that package set, because the NixOS module cannot retroactively modify an already-instantiated `pkgs`.

- [ ] **Step 3: Run every shell regression test**

Run:

```bash
bash tests/test-auto-commit-policy.sh
bash tests/test-cli-options.sh
bash tests/test-git-flake-source.sh
bash tests/test-logging-configuration.sh
bash tests/test-package-change-detection.sh
bash tests/test-preserve-handler.sh
bash tests/test-signal-list.sh
bash tests/test-singleton-lock.sh
```

Expected: each script reports its `ok:` message and exits successfully.

- [ ] **Step 4: Run static checks and Nix evaluation/builds**

Run the shell and Python static checks from the project's development shell (`nix develop`):

```bash
shellcheck src/bin/nixos-upgrade src/lib/privileged-worker \
  tests/test-auto-commit-policy.sh tests/test-cli-options.sh \
  tests/test-git-flake-source.sh tests/test-logging-configuration.sh \
  tests/test-package-change-detection.sh tests/test-preserve-handler.sh \
  tests/test-signal-list.sh tests/test-singleton-lock.sh
pyflakes src/lib
pycodestyle --max-line-length=100 src/lib
python3 -m py_compile src/lib/nixos-upgrade.py src/lib/synsignals.py src/lib/colorformatter.py
git diff --check
```

Then, from the repository root, run the Nix checks and builds:

```bash
nix flake check --no-build
nix build .#default --no-link
nix build .#dev --no-link
```

Expected: all commands exit successfully. Remove `src/lib/__pycache__/` created by Python compilation before checking the final worktree.

- [ ] **Step 5: Commit the documentation and final spec-facing changes**

```bash
git add README.md
git commit -m "docs: explain NixOS package overlay behavior"
```
