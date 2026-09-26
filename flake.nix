rec {
  description = "NixOS upgrade showing what will be changed";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs = { self, nixpkgs }:
  let
    name = "nixos-upgrade";

    # Systems supported
    systems = [
      "x86_64-linux"   # 64-bit Intel/AMD Linux
      "aarch64-linux"  # 64-bit ARM Linux
      "x86_64-darwin"  # 64-bit Intel macOS
      "aarch64-darwin" # 64-bit ARM macOS
    ];

    eachSystem = with nixpkgs.lib; (
      f: foldAttrs mergeAttrs { }
        (map (s: mapAttrs (_: v: { ${s} = v; }) (f s)) systems)
    );

    nixosUpgradeOverlay = final: _prev: {
      nixos-upgrade = final.callPackage ./package.nix { };
    };
  in eachSystem (system:
  let
    pkgs = import nixpkgs {
      inherit system;
      overlays = [ nixosUpgradeOverlay ];
    };

    python = pkgs.python3;
    pythonWithPkgs = python.withPackages (ps: with ps; [
        yaspin
        termcolor
    ]);
    pythonPackages = python.pkgs;

    runtimeInputs = with pkgs; [
      pythonWithPkgs
      git
      nvd
      nix
      man
      coreutils
      glibc.bin
      util-linux
    ];

    pyFlakes = [
      pythonPackages.pyflakes
      pythonPackages.rope
      pythonPackages.yapf
      pythonPackages.mccabe
      pythonPackages.pycodestyle
    ];

    devShellInputs = with pkgs; [
      # pylsp...
      pythonPackages.python-lsp-server

      # Nix LSP
      nil
      nixd
      statix
      deadnix

      # Toml LSP
      taplo

      # bash LSP
      bash-language-server
      shellcheck

      # Markdown LSP
      marksman

      # Pandoc
      pandoc

      # Sandboxing for agents
      bubblewrap
    ] ++ pyFlakes ++ runtimeInputs;

  in
  {
    packages = rec {
      default = pkgs.nixos-upgrade;
      dev = pkgs.callPackage ./package.nix { pyOpts = "-B -s"; };
      ${name} = default;
    };

    devShells = {
      default = pkgs.mkShell {
        packages = devShellInputs;

        # shellHook = ''
        #   # zellij session
        #   SESSION_NAME="nixos-upgrade-dev"
        #   if ! zellij list-sessions | grep -q "$SESSION_NAME"; then
        #     export EDITOR=hx
        #     exec zellij --session "$SESSION_NAME" \
        #                 --new-session-with-layout dev-layout.kdl
        #   fi
        #   zellij attach "$SESSION_NAME"
        # '';
      };
    };

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
  }) // rec {
    overlays.default = nixosUpgradeOverlay;

    nixosModules.${name} = (
      { config, lib, pkgs, ... }:
      let
        cfg = config.programs.${name};
        system = pkgs.stdenv.hostPlatform.system;
      in {
        options = {
          programs.${name} = {
            enable = lib.mkOption {
              type = lib.types.bool;
              default = false;
              description = "${name} program";
            };

            package = lib.mkOption {
              type = lib.types.nullOr lib.types.package;
              default = self.packages.${system}.default;
              description = "package to use";
            };
          };
        };

        config = lib.mkIf cfg.enable {
          nix.settings.experimental-features = ["nix-command" "flakes"];

          environment.systemPackages = (
            lib.optional (cfg.package != null) cfg.package);
        };
      }
    );

    nixosModules.default = nixosModules.${name};
  };
}
