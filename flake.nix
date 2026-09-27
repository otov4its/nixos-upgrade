{
  description = "NixOS upgrade showing what will be changed";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs = { self, nixpkgs }:
  let
    name = "nixos-upgrade";

    # Systems supported
    systems = [
      "x86_64-linux"  # 64-bit Intel/AMD Linux
      "aarch64-linux" # 64-bit ARM Linux
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

    pythonDevTools = [
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
    ] ++ pythonDevTools ++ runtimeInputs;

  in
  {
    packages = rec {
      default = pkgs.nixos-upgrade;
      dev = pkgs.callPackage ./package.nix {
        pyOpts = "-B -s";
        compilePythonBytecode = false;
      };
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
      in {
        python-unit-tests = basePkgs.runCommand "nixos-upgrade-python-unit-tests" {
          nativeBuildInputs = [ basePkgs.python3 ];
        } ''
          cd ${self.outPath}
          python3 -m unittest discover -s tests -p 'test_*.py' -v
          touch $out
        '';

        sudo-activation = basePkgs.callPackage ./tests/sudo-activation-vm.nix { };

        nix-static-analysis = basePkgs.runCommand "nixos-upgrade-nix-static-analysis" {
          nativeBuildInputs = [ basePkgs.statix basePkgs.deadnix ];
        } ''
          cd ${self.outPath}
          statix check .
          deadnix --fail .
          touch $out
        '';

        package-overlay =
          assert overlayPkgs.nixos-upgrade.drvPath
            == self.packages.${system}.default.drvPath;
          basePkgs.runCommand "nixos-upgrade-overlay-check" { }
            "touch $out";

        linux-only-outputs =
          assert !(self.packages ? x86_64-darwin);
          assert !(self.packages ? aarch64-darwin);
          assert !(self.devShells ? x86_64-darwin);
          assert !(self.devShells ? aarch64-darwin);
          basePkgs.runCommand "nixos-upgrade-linux-only-outputs-check" { }
            "touch $out";

        nixos-module-package-selection =
          assert hostSystem.options.programs.nixos-upgrade.enable.description
            == "Whether to enable nixos-upgrade.";
          assert hostSystem.options.programs.nixos-upgrade.package.description
            == "The nixos-upgrade package to use. Set to null to skip installing the package.";
          assert !(builtins.elem "nix-command"
            (hostSystem.config.nix.settings.experimental-features or []));
          assert !(builtins.elem "flakes"
            (hostSystem.config.nix.settings.experimental-features or []));
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
          basePkgs.runCommand "nixos-upgrade-nixos-module-check" { }
            "touch $out";
      }
    else { };
  }) // rec {
    overlays.default = nixosUpgradeOverlay;

    nixosModules.${name} =
      { config, lib, pkgs, ... }:
      let
        cfg = config.programs.${name};
      in {
        options = {
          programs.${name} = {
            enable = lib.mkEnableOption name;

            package = lib.mkPackageOption pkgs name {
              nullable = true;
              extraDescription = "Set to null to skip installing the package.";
            };
          };
        };

        config = lib.mkIf cfg.enable {
          nixpkgs.overlays = [ self.overlays.default ];

          environment.systemPackages =
            lib.optional (cfg.package != null) cfg.package;
        };
      };

    nixosModules.default = nixosModules.${name};
  };
}
