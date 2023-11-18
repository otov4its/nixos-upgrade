# nixos-upgrade

![screenshot]

NixOS upgrade showing what will be changed.

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

## NixOs flake.nix

```nix
{
    inputs = {
        # ...
        
        nixos-upgrade = {
          url = "github:otov4its/nixos-upgrade/stable";
          # Optionally
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

# Developing

```bash
$ nix develop
$ nix build .#dev
$ ./result-dev/bin/nixos-upgrade
```

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
