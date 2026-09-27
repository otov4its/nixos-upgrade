---
title: "@name@"
section: 8
header: System Administration
footer: "@name@ @version@"
---

# NAME
@name@ - @description@

# SYNOPSIS
`@name@` [OPTION]...

# DESCRIPTION
Run `@name@` as the invoking user, without `sudo`. Flake preparation, builds,
diffs, and confirmation run with that user's credentials. Only after a
positive confirmation does the program invoke its fixed-purpose activation
helper through `sudo` to switch the system and perform requested lock-file or
repository updates.

Sudo authorization is controlled by the host and may prompt at activation
time. The package does not install sudoers rules. `--assume-yes` skips the
upgrade confirmation but does not bypass sudo authorization.

# OPTIONS
`-h, --help`
: show this help message and exit

`-V, --version`
: show `@name@` version

`--flake=`*DIR*
: flake dir with nixos configuration (default: */etc/nixos/*). When *DIR* is
  inside a Git repository, Nix uses files indexed by Git; add new files to the
  index before upgrading.

`-u, --no-update-lock-file`
: do not update flake.lock

`-m` *MESSAGE*, `--commit-message=`*MESSAGE*
: add a commit message

`-c, --no-commit`
: do not commit a flake repo

`-y, --assume-yes`
: when a yes/no prompt would be presented, assume that the user entered "y".
  In particular, suppresses the prompt that appears when upgrading system.
  Mutually exclusive with `-n, --assume-no`.

`-n, --assume-no`
: likewise `--assume-yes`, but no. Mutually exclusive with `-y, --assume-yes`.

`-v, --verbose`
: increase verbosity

`-q, --quiet`
: decrease verbosity

`--color=`*auto*|*always*|*never*
: when to display output using colors (default: *auto*)

# SEE ALSO
`nixos-rebuild`(8), `nix`(1), `nvd`(1) 
