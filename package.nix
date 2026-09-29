{ lib, stdenv, stdenvNoCC, python3, pandoc, git, nvd, nix, coreutils,
  glibc, getent, util-linux, jq, shellcheck,
  pyOpts ? "-B -s -OO -E -Wignore --check-hash-based-pycs never",
  compilePythonBytecode ? true }:

let
  name = "nixos-upgrade";
  version = "2026-07-01-1.0.5";
  description = "NixOS upgrade showing what will be changed";

  binSrc = "./bin/${name}";
  outBin = "$out/${binSrc}";
  outLibDir = "$out/lib";
  manPage = "./share/man/man8/${name}.8";
  manPageMd = "${manPage}.md";

  pythonWithPkgs = python3.withPackages (ps: with ps; [
    yaspin
    termcolor
  ]);
  pythonPackages = python3.pkgs;

  runtimeInputs = [
    pythonWithPkgs
    git
    nvd
    nix
    coreutils
    glibc.bin
    getent
    util-linux
    jq
  ];

  pythonDevTools = [
    pythonPackages.pyflakes
    pythonPackages.rope
    pythonPackages.yapf
    pythonPackages.mccabe
    pythonPackages.pycodestyle
  ];
in
stdenvNoCC.mkDerivation rec {
  pname = name;
  inherit version;
  src = ./src;

  nativeBuildInputs = [
    python3
    pandoc
  ];

  preBuild = ''
    substituteInPlace ./${manPageMd} \
      --replace-fail "@name@" "${name}" \
      --replace-fail "@version@" "${version}" \
      --replace-fail "@description@" "${description}"

    substituteInPlace ${binSrc} \
      --replace-fail "@version@" "${version}" \
      --replace-fail "@name@" "${name}" \
      --replace-fail "@path@" "${lib.makeBinPath runtimeInputs}" \
      --replace-fail "@pyfile@" "${outLibDir}/${name}.py"

    substituteInPlace ./lib/activation.py \
      --replace-fail "@helper@" "${outLibDir}/nixos-upgrade-activate"

    substituteInPlace ./lib/nixos-upgrade-activate \
      --replace-fail "#!/usr/bin/env bash" "#!${stdenv.shell}" \
      --replace-fail "@path@" "${lib.makeBinPath runtimeInputs}"
  '';

  postBuild = ''
    substituteInPlace ${binSrc} \
      --replace-fail "@py_opts@" "${pyOpts}"
  '';

  buildPhase = ''
    runHook preBuild

    ${lib.optionalString compilePythonBytecode ''
      python -m compileall -f -o 2 --invalidation-mode unchecked-hash ./lib
    ''}

    # Man page
    pandoc ./${manPageMd} --standalone --to=man --output=./${manPage}
    gzip ./${manPage}
    rm ./${manPageMd}

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    cp -R . $out

    runHook postInstall
  '';

  doInstallCheck = true;
  nativeInstallCheckInputs = [ shellcheck ] ++ pythonDevTools;
  installCheckPhase = ''
    runHook preCheck

    ${stdenv.shellDryRun} ${outBin}
    shellcheck ${outBin}

    expected_launcher_shebang="#!${stdenv.shell}"
    IFS= read -r actual_launcher_shebang < "${outBin}"
    if [[ "$actual_launcher_shebang" != "$expected_launcher_shebang" ]]; then
      printf 'launcher shebang is not pinned to stdenv.shell\n' >&2
      exit 1
    fi

    ${stdenv.shellDryRun} "${outLibDir}/nixos-upgrade-activate"
    shellcheck --shell=bash ${outLibDir}/nixos-upgrade-activate
    shellcheck --shell=bash "${src}/lib/nixos-upgrade-activate"

    expected_activation_shebang="#!${stdenv.shell}"
    IFS= read -r actual_activation_shebang < "${outLibDir}/nixos-upgrade-activate"
    if [[ "$actual_activation_shebang" != "$expected_activation_shebang" ]]; then
      printf 'activation helper shebang is not pinned to stdenv.shell\n' >&2
      exit 1
    fi

    pyflakes ${outLibDir}

    runHook postCheck
  '';
}
