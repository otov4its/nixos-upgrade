# nixos-upgrade

![screenshot]

NixOS upgrade showing what will be changed.

# Running

Run `nixos-upgrade` as your regular user, not with `sudo`. Flake updates,
builds, diffs, and the confirmation run with your credentials. Only after you
confirm the proposed upgrade does the application invoke its fixed-purpose
helper through `sudo` to activate the system and, when requested, publish the
lock file and commit repository changes.

A password prompt depends on the host's sudo policy, which must authorize the
helper. The package does not install sudoers rules. `--assume-yes` skips the
upgrade confirmation only; it does not bypass sudo authorization.

# Installation

Use one or more of the following options:

## nix run

```bash
$ nix run github:otov4its/nixos-upgrade/stable
```

## nix shell

```bash
$ nix shell github:otov4its/nixos-upgrade/stable
```

## nix profile

```bash
$ nix profile install github:otov4its/nixos-upgrade/stable
```

## NixOS flake.nix

```nix
{
    inputs = {
        # ...
        
        nixos-upgrade = {
          url = "github:otov4its/nixos-upgrade/stable";
          # Optional: aligns standalone outputs with the host revision and can
          # deduplicate the lock graph. Not required for the module's host package.
          inputs.nixpkgs.follows = "nixpkgs";
        }
    };
    
    outputs = { self, ... }@inputs:
    {
        nixosConfigurations = {
            # ...

            modules = [
                # ...
                
                inputs.nixos-upgrade.nixosModules.default
                {
                    programs.nixos-upgrade.enable = true;
                }

            ];
        }
    }
}
```

When enabled, the NixOS module adds `inputs.nixos-upgrade.overlays.default` to the host's package set and defaults `programs.nixos-upgrade.package` to `pkgs.nixos-upgrade`. This means the package uses dependencies from the host's Nixpkgs configuration. The host's `nix` executable must support the CLI options used by `nixos-upgrade`; if its version is incompatible, select the flake's pinned standalone package explicitly:

```nix
programs.nixos-upgrade.package =
  inputs.nixos-upgrade.packages.${pkgs.stdenv.hostPlatform.system}.default;
```

If your configuration supplies an already-instantiated `nixpkgs.pkgs`, the module cannot apply its overlay to that package set retroactively. Apply the overlay while constructing `pkgs`:

```nix
nixpkgs.pkgs = import inputs.nixpkgs {
  system = "x86_64-linux"; # Use your target system.
  overlays = [ inputs.nixos-upgrade.overlays.default ];
};
```

# Developing

```bash
$ nix develop
$ nix build .#dev
$ ./result-dev/bin/nixos-upgrade
```

Maintainers can find the [project architecture guide](docs/architecture.md).

# Changelog

See [CHANGELOG]

# Contributing

Your PRs are welcome and greatly appreciated.

# License

Distributed under the MIT License. See [LICENSE] for more information.

# Acknowledgements

- [nix - the purely functional package manager][nix]
- [nvd - Nix/NixOS package version diff tool][nvd]


[LICENSE]: LICENSE
[CHANGELOG]: CHANGELOG.md
[screenshot]: screenshot.png
[nix]: https://github.com/NixOS/nix
[nvd]: https://gitlab.com/khumba/nvd
