rec {
  description = "NixOS upgrade showing what will be changed";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-23.05";
  };

  outputs = { self, nixpkgs }:
  let
    name = "nixos-upgrade";

    dateVer = "2023-11-16";
    semVer = "1.0.1-rc";
    packageVersion = "${dateVer}-${semVer}";

    packageSrc = ./src;

    binSrc = "./bin/${name}";
    outBin = "$out/${binSrc}";
    outLibDir = "$out/lib";
    manPage = "./share/man/man8/${name}.8";
    manPageMd = "${manPage}.md";
    manPageGz = "${manPage}.gz";

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
  in eachSystem (system:
  let
    pkgs = import nixpkgs { inherit system; };

    python = pkgs.python311;
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
      # ...with providers
      pyFlakes
      # Helix code editor
      helix
      # Nix LSP for Helix
      nil
      # Toml LSP
      taplo
      # bash LSP
      nodePackages.bash-language-server
      shellcheck
      # Markdown LSP
      marksman
      # Pandoc
      pandoc
      # Fish shell
      fish
      zellij
    ] ++ runtimeInputs;

    pyOptsDev = "-B -s";
    pyOptsProd = "-B -s -OO -E -Wignore --check-hash-based-pycs never";
  in
  rec {
    packages = rec {
      default = pkgs.stdenvNoCC.mkDerivation rec {
        pname = name;
        version = packageVersion;
        src = packageSrc;

        nativeBuildInputs = with pkgs; [
          python
          pandoc
        ];

        preBuild = ''
          substituteInPlace ./${manPageMd} \
            --replace "@name@" "${name}" \
            --replace "@version@" "${version}" \
            --replace "@description@" "${description}" \

          substituteInPlace ${binSrc} \
            --replace "@man@" "$out/${manPageGz}" \
            --replace "@version@" "${version}" \
            --replace "@name@" "${name}" \
            --replace "@path@" "${pkgs.lib.makeBinPath runtimeInputs}" \
            --replace "@worker@" "${outLibDir}/privileged-worker" \
            --replace "@pyfile@" "${outLibDir}/${name}.py" \
        '';

        postBuild = ''
          substituteInPlace ${binSrc} \
            --replace "@py_opts@" "${pyOptsProd}"
        '';

        buildPhase = ''
          runHook preBuild

          python -m compileall -f -o 2 --invalidation-mode unchecked-hash ./lib

          # Man page
          pandoc ./${manPageMd} --standalone --to=man --output=./${manPage}
          gzip ./${manPage}
          rm ./${manPageMd}

          runHook postBuild
        '';

        buildInputs = runtimeInputs;
        installPhase = ''
          runHook preInstall

          cp -R . $out

          runHook postInstall
        '';

        doInstallCheck = true;
        nativeInstallCheckInputs = [ pkgs.shellcheck pyFlakes ];
        installCheckPhase = ''
          runHook preCheck

          ${pkgs.stdenv.shellDryRun} ${outBin}
          shellcheck ${outBin}

          ${pkgs.stdenv.shellDryRun} "${outLibDir}/privileged-worker"
          shellcheck ${outLibDir}/privileged-worker

          pyflakes ${outLibDir}

          runHook postCheck
        '';
      };

      dev = default.overrideAttrs (finalAttrs: prevAttrs: {
        postBuild = ''
          substituteInPlace ${binSrc} \
            --replace "@py_opts@" "${pyOptsDev}"
        '';
      });

      ${name} = default;
    };
    
    devShells = {
      default = pkgs.mkShell {
        packages = devShellInputs;

        shellHook = ''
          # zellij session
          export EDITOR=hx
          exec zellij --session update-nixos-dev \
            --layout dev-layout.kdl
        '';
      };
    };
  }) // rec {
    nixosModules.${name} = (
      { config, lib, pkgs, ... }:
      let
        cfg = config.programs.${name};
        system = pkgs.system;
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
