{ lib, stdenv, stdenvNoCC, python3, pandoc, git, nvd, nix, man, coreutils,
  glibc, util-linux, shellcheck,
  pyOpts ? "-B -s -OO -E -Wignore --check-hash-based-pycs never" }:

let
  name = "nixos-upgrade";
  version = "2026-07-01-1.0.5";
  description = "NixOS upgrade showing what will be changed";

  binSrc = "./bin/${name}";
  outBin = "$out/${binSrc}";
  outLibDir = "$out/lib";
  manPage = "./share/man/man8/${name}.8";
  manPageMd = "${manPage}.md";
  manPageGz = "${manPage}.gz";

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
      --replace "@name@" "${name}" \
      --replace "@version@" "${version}" \
      --replace "@description@" "${description}"

    substituteInPlace ${binSrc} \
      --replace "@man@" "$out/${manPageGz}" \
      --replace "@version@" "${version}" \
      --replace "@name@" "${name}" \
      --replace "@path@" "${lib.makeBinPath runtimeInputs}" \
      --replace "@worker@" "${outLibDir}/privileged-worker" \
      --replace "@pyfile@" "${outLibDir}/${name}.py"
  '';

  postBuild = ''
    substituteInPlace ${binSrc} \
      --replace "@py_opts@" "${pyOpts}"
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
  nativeInstallCheckInputs = [ shellcheck ] ++ pyFlakes;
  installCheckPhase = ''
    runHook preCheck

    ${stdenv.shellDryRun} ${outBin}
    shellcheck ${outBin}

    ${stdenv.shellDryRun} "${outLibDir}/privileged-worker"
    shellcheck ${outLibDir}/privileged-worker

    pyflakes ${outLibDir}

    runHook postCheck
  '';
}
