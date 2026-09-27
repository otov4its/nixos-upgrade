{ pkgs, ... }:
let
  fakeNix = pkgs.runCommand "nixos-upgrade-test-nix" { } ''
    mkdir -p "$out/bin"

    cat > "$out/bin/nix-env" <<'EOF'
    #!${pkgs.runtimeShell}
    set -eu
    test "$1" = --profile
    profile="$2"
    test "$3" = --set
    closure="$4"
    printf 'profile-set\n' >> /run/nixos-upgrade-test-events
    case "$closure" in
      *profile-failed-closure*)
        exit 31
        ;;
    esac
    mkdir -p "$(dirname "$profile")"
    ln -sfnT -- "$closure" "$profile"
    EOF
    chmod +x "$out/bin/nix-env"

    cat > "$out/bin/readlink" <<'EOF'
    #!${pkgs.runtimeShell}
    if [[ "$*" == *"/run/current-system"* ]] &&
       [[ -e /run/nixos-upgrade-test-switched ]]; then
      printf '%s' /nix/store/nixos-upgrade-test-changed-system
      exit 0
    fi
    exec ${pkgs.coreutils}/bin/readlink "$@"
    EOF
    chmod +x "$out/bin/readlink"

    cat > "$out/bin/nix" <<'EOF'
    #!${pkgs.runtimeShell}
    set -eu
    test "$1" = --extra-experimental-features
    test "$2" = 'nix-command flakes'
    shift 2

    if [[ "$1" == flake && "$2" == update ]]; then
      shift 2
      flake_dir=
      output_lock=
      while (($#)); do
        case "$1" in
          --flake)
            flake_dir=$2
            shift 2
            ;;
          --output-lock-file)
            output_lock=$2
            shift 2
            ;;
          *)
            echo "unexpected nix flake update argument: $1" >&2
            exit 81
            ;;
        esac
      done
      test "$flake_dir" = /etc/nixos
      test -n "$output_lock"
      printf 'nix-update:%s\n' "$(id -u)" >> /run/nixos-upgrade-test-events
      printf 'user workflow lock\n' > "$output_lock"
      exit 0
    fi

    if [[ "$1" == build ]]; then
      shift
      out_link=
      reference_lock=
      config=
      while (($#)); do
        case "$1" in
          --out-link)
            out_link=$2
            shift 2
            ;;
          --reference-lock-file)
            reference_lock=$2
            shift 2
            ;;
          --no-write-lock-file)
            shift
            ;;
          *)
            config=$1
            shift
            ;;
        esac
      done
      test -n "$out_link"
      test -f "$reference_lock"
      cat "$reference_lock" > /run/nixos-upgrade-test-reference-lock
      [[ "$config" == /etc/nixos#nixosConfigurations.* ]]
      printf 'nix-build:%s\n' "$(id -u)" >> /run/nixos-upgrade-test-events
      if [[ -e /run/nixos-upgrade-test-slow-build ]]; then
        printf '%s\n' "$$" > /run/nixos-upgrade-test-build-pid
        printf '%s\n' "$PPID" > /run/nixos-upgrade-test-controller-pid
        sleep 300 &
        child_pid=$!
        trap 'kill -TERM "$child_pid" 2>/dev/null || true; wait "$child_pid" 2>/dev/null || true; exit 143' TERM
        printf '%s\n' "$child_pid" > /run/nixos-upgrade-test-build-child-pid
        wait "$child_pid"
      fi
      closure='@CANDIDATE@'
      if [[ -e /run/nixos-upgrade-test-slow-activation ]]; then
        closure='@SLOW_CANDIDATE@'
      fi
      ln -sfnT -- "$closure" "$out_link"
      exit 0
    fi

    echo "unexpected nix command: $*" >&2
    exit 99
    EOF
    substituteInPlace "$out/bin/nix" \
      --replace-fail '@CANDIDATE@' '${successfulClosure}' \
      --replace-fail '@SLOW_CANDIDATE@' '${slowClosure}'
    chmod +x "$out/bin/nix"
  '';

  fakeNvd = pkgs.runCommand "nixos-upgrade-test-nvd" { } ''
    mkdir -p "$out/bin"
    cat > "$out/bin/nvd" <<'EOF'
    #!${pkgs.runtimeShell}
    set -eu
    test "$1" = --color=always
    test "$2" = diff
    printf 'nvd:%s\n' "$(id -u)" >> /run/nixos-upgrade-test-events
    printf 'Comparing system closures\nPackages\n[A.] test-package 1 -> 2\n'
    EOF
    chmod +x "$out/bin/nvd"
  '';

  testPackage = pkgs.callPackage ../package.nix {
    nix = fakeNix;
    nvd = fakeNvd;
  };

  makeClosure = name: mode: delay: pkgs.runCommand name { } ''
    mkdir -p "$out/bin"
    cat > "$out/bin/switch-to-configuration" <<'EOF'
    #!${pkgs.runtimeShell}
    set -eu
    test "$1" = switch
    cat /etc/nixos/flake.lock > /run/nixos-upgrade-test-lock-during-switch

    signal_process=$$
    while [[ "$signal_process" -gt 1 ]]; do
      signal_parent=1
      process_ignored=
      while IFS= read -r status_line; do
        case "$status_line" in
          PPid:*) read -r _ signal_parent <<< "$status_line" ;;
          SigIgn:*) read -r _ process_ignored <<< "$status_line" ;;
        esac
      done < "/proc/$signal_process/status"
      process_name=$(< "/proc/$signal_process/comm")
      IFS=' ' read -r _ _ _ _ process_group _ < "/proc/$signal_process/stat"
      printf '%s:%s:%s:%s:%s\n' "$signal_process" "$process_name" "$signal_parent" "$process_ignored" "$process_group" >> /run/nixos-upgrade-test-signal-processes
      signal_process=$signal_parent
    done
    printf 'switch-start\n' >> /run/nixos-upgrade-test-events
    touch /run/nixos-upgrade-test-switch-started
    sleep @DELAY@
    if [[ '@MODE@' == fail ]]; then
      exit 42
    fi
    printf 'switch-complete\n' >> /run/nixos-upgrade-test-events
    touch /run/nixos-upgrade-test-switched
    EOF
    substituteInPlace "$out/bin/switch-to-configuration" \
      --replace-fail '@MODE@' '${mode}' \
      --replace-fail '@DELAY@' '${delay}'
    chmod +x "$out/bin/switch-to-configuration"
  '';

  successfulClosure = makeClosure "nixos-upgrade-test-system" "success" "0.1";
  slowClosure = makeClosure "nixos-upgrade-test-slow-system" "success" "2";
  failedClosure = makeClosure "nixos-upgrade-test-switch-failed-system" "fail" "0.1";
  profileFailedClosure = makeClosure "nixos-upgrade-test-profile-failed-closure" "success" "0.1";

  activationHelper = "${testPackage}/lib/nixos-upgrade-activate";
  systemClosure = "${successfulClosure}";
  slowSystemClosure = "${slowClosure}";
  failedSystemClosure = "${failedClosure}";
  profileFailedSystemClosure = "${profileFailedClosure}";
in
pkgs.testers.runNixOSTest {
  name = "nixos-upgrade-sudo-activation";

  nodes.machine = _: {
    users.users.upgrader = {
      isNormalUser = true;
      home = "/home/upgrader";
      createHome = true;
    };
    users.users.denied-upgrader = {
      isNormalUser = true;
      home = "/home/denied-upgrader";
      createHome = true;
    };

    security.sudo.extraRules = [
      {
        users = [ "upgrader" ];
        commands = [
          {
            command = activationHelper;
            options = [ "NOPASSWD" ];
          }
        ];
      }
    ];

    environment.systemPackages = [
      testPackage
      pkgs.git
      pkgs.util-linux
    ];
  };

  testScript = ''
    import base64
    import json
    import shlex
    import time

    start_all()

    helper = ${builtins.toJSON activationHelper}
    candidate = ${builtins.toJSON systemClosure}
    slow_candidate = ${builtins.toJSON slowSystemClosure}
    failed_candidate = ${builtins.toJSON failedSystemClosure}
    profile_failed_candidate = ${builtins.toJSON profileFailedSystemClosure}
    helper_user = "upgrader"
    flake_dir = "/etc/nixos"
    profile = "/nix/var/nix/profiles/system"

    machine.wait_for_unit("multi-user.target")
    machine.succeed("test -x " + shlex.quote(helper))
    machine.succeed("git config --global user.name 'NixOS Repository Owner'")
    machine.succeed("git config --global user.email 'nixos-owner@example.invalid'")
    machine.succeed(
        "mkdir -p /etc/nixos && cd /etc/nixos && "
        "git init -q && printf 'flake source\n' > flake.nix && "
        "printf 'original lock\n' > flake.lock && "
        "git add flake.nix flake.lock && git commit -q -m initial"
    )
    machine.succeed(
        "cat > /etc/nixos/.git/hooks/post-commit <<'EOF'\n"
        "#!/bin/sh\n"
        "printf 'commit\\n' >> /run/nixos-upgrade-test-events\n"
        "printf '%s\\n' \"$(id -u)\" > /run/nixos-upgrade-test-commit-uid\n"
        "printf '%s\\n' \"$HOME\" > /run/nixos-upgrade-test-commit-home\n"
        "EOF\n"
        "chmod +x /etc/nixos/.git/hooks/post-commit"
    )

    machine.succeed(
        "touch /run/nixos-upgrade-test-events /run/nixos-upgrade-test-reference-lock "
        "/run/nixos-upgrade-test-build-pid "
        "/run/nixos-upgrade-test-controller-pid /run/nixos-upgrade-test-build-child-pid && "
        "chmod 0666 /run/nixos-upgrade-test-events /run/nixos-upgrade-test-reference-lock "
        "/run/nixos-upgrade-test-build-pid "
        "/run/nixos-upgrade-test-controller-pid /run/nixos-upgrade-test-build-child-pid"
    )
    original_system = machine.succeed("readlink -e /run/current-system").strip()

    app = ${builtins.toJSON "${testPackage}/bin/nixos-upgrade"}

    def app_command(user, *options):
        runtime_dir = "/run/user/" + machine.succeed("id -u " + user).strip()
        args = [
            "runuser", "-u", user, "--", "env",
            "XDG_RUNTIME_DIR=" + runtime_dir,
            app, "--flake", flake_dir, *options,
        ]
        return shlex.join(args) + " < /dev/null"

    def execute_app(user, *options):
        output_file = "/run/nixos-upgrade-app-output"
        command = app_command(user, *options)
        wrapped = (
            "if " + command + " > " + shlex.quote(output_file) + " 2>&1; "
            + "then result=0; else result=$?; fi; "
            + "printf '__APP_EXIT_STATUS__%s\\n' $result; "
            + "cat " + shlex.quote(output_file)
        )
        transport_status, output = machine.execute(wrapped)
        assert transport_status == 0, output
        lines = output.splitlines()
        status_line = next(
            line for line in lines if line.startswith("__APP_EXIT_STATUS__")
        )
        app_output = "\\n".join(
            line for line in lines if not line.startswith("__APP_EXIT_STATUS__")
        )
        return int(status_line.removeprefix("__APP_EXIT_STATUS__")), app_output

    def launch_app_background(label, user, *options):
        prefix = "/run/nixos-upgrade-" + label
        output_file = prefix + ".out"
        pgid_file = prefix + ".pgid"
        status_file = prefix + ".status"
        # Async shells start with SIGINT ignored; reset it so this models a
        # foreground invocation before the process group receives terminal SIGINT.
        runner = (
            "printf '%s\\n' $$ > " + shlex.quote(pgid_file)
            + "; exec env --default-signal=INT " + app_command(user, *options)
        )
        machine.succeed(
            "( setsid --wait sh -c " + shlex.quote(runner)
            + " > " + shlex.quote(output_file) + " 2>&1 < /dev/null & "
            + "app_pid=$!; "
            + "if wait $app_pid; then result=0; else result=$?; fi; "
            + "printf '%s\\n' $result > " + shlex.quote(status_file)
            + ") < /dev/null > /dev/null 2>&1 &"
        )
        machine.wait_until_succeeds("test -f " + shlex.quote(pgid_file), timeout=10)
        return output_file, pgid_file, status_file

    for user in (helper_user, "denied-upgrader"):
        runtime_dir = "/run/user/" + machine.succeed("id -u " + user).strip()
        machine.succeed(
            "mkdir -p " + shlex.quote(runtime_dir) + " && "
            "chown " + shlex.quote(user) + " " + shlex.quote(runtime_dir) + " && "
            "chmod 0700 " + shlex.quote(runtime_dir)
        )

    def profile_target():
        quoted_profile = shlex.quote(profile)
        return machine.succeed(
            "if test -e " + quoted_profile + "; then readlink -e "
            + quoted_profile + "; else printf absent; fi"
        ).strip()

    # The installed application runs as the normal user and can decline before
    # requesting sudo. Nix and NVD execute as that user; the profile and repo
    # remain unchanged and no privileged helper work occurs.
    upgrader_uid = machine.succeed("id -u " + helper_user).strip()
    profile_before_decline = profile_target()
    status, output = execute_app(
        helper_user, "--assume-no", "--color=never"
    )
    assert status == 0, output
    assert "nothing changed" in output, output
    assert machine.succeed("cat /run/nixos-upgrade-test-events").splitlines() == [
        "nix-update:" + upgrader_uid,
        "nix-build:" + upgrader_uid,
        "nvd:" + upgrader_uid,
    ]
    assert profile_target() == profile_before_decline
    assert machine.succeed("cat /etc/nixos/flake.lock").strip() == "original lock"
    assert machine.succeed("git -C /etc/nixos status --porcelain").strip() == ""

    def request_payload(lock_bytes=None, message="test auto commit", no_commit=False):
        return json.dumps({
            "lock_file_base64": (
                base64.b64encode(lock_bytes).decode("ascii")
                if lock_bytes is not None else None
            ),
            "commit_message_base64": (
                base64.b64encode(message.encode("utf-8")).decode("ascii")
                if not no_commit else None
            ),
        }, separators=(",", ":"))

    def activation_command(closure, expected=original_system, lock_bytes=None,
                          message="test auto commit", no_commit=False,
                          raw_payload=None, user=helper_user):
        args = [
            "/run/wrappers/bin/sudo", "-n", helper, "activate",
            "--expected-current", expected,
            "--system-closure", closure,
            "--flake-dir", flake_dir,
        ]
        if no_commit:
            args.append("--no-commit")
        payload = raw_payload if raw_payload is not None else request_payload(
            lock_bytes, message, no_commit
        )
        return (
            "printf '%s' " + shlex.quote(payload) + " | runuser -u "
            + shlex.quote(user) + " -- " + shlex.join(args)
            + " 2>/run/nixos-upgrade-activation-helper.stderr"
        )

    def execute_activation(command):
        status, output = machine.execute(command)
        stderr = machine.succeed(
            "if test -f /run/nixos-upgrade-activation-helper.stderr; then "
            "cat /run/nixos-upgrade-activation-helper.stderr; "
            "else echo 'helper stderr capture missing'; fi"
        )
        return status, json.loads(output.strip()), output + stderr

    def reset_switch_markers():
        machine.succeed(
            "rm -f /run/nixos-upgrade-test-switched "
            "/run/nixos-upgrade-test-switch-started "
            "/run/nixos-upgrade-activation-helper.stderr "
            "/run/nixos-upgrade-test-commit-uid "
            "/run/nixos-upgrade-test-commit-home "
            "/run/nixos-upgrade-test-lock-during-switch "
            "/run/nixos-upgrade-test-signal-processes"
        )
        machine.succeed(
            ": > /run/nixos-upgrade-test-events && "
            ": > /run/nixos-upgrade-test-reference-lock && "
            ": > /run/nixos-upgrade-test-build-pid && "
            ": > /run/nixos-upgrade-test-controller-pid && "
            ": > /run/nixos-upgrade-test-build-child-pid && "
            "chmod 0666 /run/nixos-upgrade-test-events /run/nixos-upgrade-test-reference-lock "
            "/run/nixos-upgrade-test-build-pid "
            "/run/nixos-upgrade-test-controller-pid /run/nixos-upgrade-test-build-child-pid"
        )

    # A confirmed transaction updates the profile, switches, publishes the
    # lock, and commits tracked changes as the repository owner.
    reset_switch_markers()
    machine.succeed("printf 'tracked change\n' >> /etc/nixos/flake.nix")
    status, result, output = execute_activation(activation_command(
        candidate,
        lock_bytes=b'new lock\n',
        message="test auto commit\n$(touch /run/nixos-upgrade-pwned)",
    ))
    assert status == 0, output
    assert result == {
        "system": "switched",
        "lock": "published",
        "commit": "committed",
    }, (result, output)
    assert machine.succeed("cat /etc/nixos/flake.lock").strip() == "new lock"
    assert machine.succeed("git -C /etc/nixos log -1 --format=%an").strip() == "NixOS Repository Owner"
    assert machine.succeed("cat /run/nixos-upgrade-test-commit-uid").strip() == "0"
    assert machine.succeed("cat /run/nixos-upgrade-test-commit-home").strip() == "/root"
    assert "test auto commit" in machine.succeed("git -C /etc/nixos log -1 --format=%B")
    assert machine.succeed(
        "test ! -e /run/nixos-upgrade-pwned && echo safe || echo marker-present"
    ).strip() == "safe", machine.succeed(
        "ls -ln /run/nixos-upgrade-pwned 2>&1 || true; "
        "git -C /etc/nixos log -1 --format=%B"
    )
    events = machine.succeed("cat /run/nixos-upgrade-test-events").splitlines()
    assert events.index("profile-set") < events.index("switch-start")
    assert events.index("switch-complete") < events.index("commit")

    # A successful switch with no changes must not create an empty commit.
    reset_switch_markers()
    commit_count = machine.succeed("git -C /etc/nixos rev-list --count HEAD").strip()
    status, result, output = execute_activation(activation_command(candidate))
    assert status == 0, output
    assert result["commit"] == "no-changes", result
    assert machine.succeed("git -C /etc/nixos rev-list --count HEAD").strip() == commit_count

    # --no-commit leaves tracked changes uncommitted and does not request a message.
    reset_switch_markers()
    machine.succeed("printf 'left uncommitted\n' >> /etc/nixos/flake.nix")
    status, result, output = execute_activation(activation_command(
        candidate, no_commit=True
    ))
    assert status == 0, output
    assert result["commit"] == "not-requested", result
    assert machine.succeed(
        "git -C /etc/nixos diff --quiet && echo clean || echo dirty"
    ).strip() == "dirty"

    # A symlink destination is atomically replaced; its target is not followed.
    reset_switch_markers()
    machine.succeed(
        "git -C /etc/nixos checkout -- flake.nix && "
        "printf 'outside lock target\n' > /run/nixos-upgrade-test-lock-target && "
        "rm -f /etc/nixos/flake.lock && "
        "ln -s /run/nixos-upgrade-test-lock-target /etc/nixos/flake.lock"
    )
    status, result, output = execute_activation(activation_command(
        candidate, lock_bytes=b'atomic lock\n', no_commit=True
    ))
    assert status == 0, output
    assert result["lock"] == "published", result
    assert machine.succeed(
        "test ! -L /etc/nixos/flake.lock && echo replaced"
    ).strip() == "replaced"
    assert machine.succeed("cat /run/nixos-upgrade-test-lock-target").strip() == "outside lock target"
    assert machine.succeed("cat /etc/nixos/flake.lock").strip() == "atomic lock"

    # Invalid input and stale builds must not modify the system profile.
    profile_before = machine.succeed("readlink -e " + shlex.quote(profile)).strip()
    status, result, output = execute_activation(activation_command(
        candidate,
        raw_payload='{"lock_file_base64":"%%%","commit_message_base64":null}',
        no_commit=True,
    ))
    assert status != 0, output
    assert result["system"] == "invalid-request", result
    assert machine.succeed("readlink -e " + shlex.quote(profile)).strip() == profile_before

    status, result, output = execute_activation(activation_command(
        candidate, expected="/nix/store/stale-system", no_commit=True
    ))
    assert status != 0, output
    assert result["system"] == "stale", result
    assert machine.succeed("readlink -e " + shlex.quote(profile)).strip() == profile_before

    reset_switch_markers()
    status, result, output = execute_activation(activation_command(
        "/tmp/not-a-store-closure", no_commit=True
    ))
    assert status != 0, output
    assert result["system"] == "invalid-request", result
    status, result, output = execute_activation(activation_command(
        candidate,
        raw_payload='{"lock_file_base64":null,"commit_message_base64":null,"extra":true}',
        no_commit=True,
    ))
    assert status != 0, output
    assert result["system"] == "invalid-request", result

    # A profile update failure and a switch failure never publish or commit.
    reset_switch_markers()
    lock_before = machine.succeed("cat /etc/nixos/flake.lock").strip()
    status, result, output = execute_activation(activation_command(
        profile_failed_candidate, lock_bytes=b'should not publish\n'
    ))
    assert status != 0, output
    assert result["system"] == "profile-failed", result
    assert machine.succeed("cat /etc/nixos/flake.lock").strip() == lock_before

    reset_switch_markers()
    status, result, output = execute_activation(activation_command(
        failed_candidate, lock_bytes=b'should not publish\n'
    ))
    assert status != 0, output
    assert result["system"] == "switch-failed", result
    assert result["lock"] == "not-run", result
    assert result["commit"] == "not-run", result
    assert machine.succeed("cat /etc/nixos/flake.lock").strip() == lock_before

    # Two same-generation requests serialize; the second sees stale state
    # after waiting for the first to finish.
    reset_switch_markers()
    expected = machine.succeed("readlink -e /run/current-system").strip()
    first = "setsid sh -c " + shlex.quote(
        activation_command(slow_candidate, expected=expected, no_commit=True)
    )
    second = "setsid sh -c " + shlex.quote(
        activation_command(slow_candidate, expected=expected, no_commit=True)
    )
    machine.succeed(
        "( if " + first + " > /run/nixos-upgrade-first.out; then "
        "printf '0\\n' > /run/nixos-upgrade-first.status; else "
        "printf '%s\\n' \"$?\" > /run/nixos-upgrade-first.status; fi ) "
        "< /dev/null > /dev/null 2>&1 &"
    )
    machine.wait_until_succeeds("test -f /run/nixos-upgrade-test-switch-started", timeout=20)
    machine.succeed(
        "( if " + second + " > /run/nixos-upgrade-second.out; then "
        "printf '0\\n' > /run/nixos-upgrade-second.status; else "
        "printf '%s\\n' \"$?\" > /run/nixos-upgrade-second.status; fi ) "
        "< /dev/null > /dev/null 2>&1 &"
    )
    time.sleep(0.25)
    assert machine.succeed(
        "test ! -e /run/nixos-upgrade-second.status && echo blocked"
    ).strip() == "blocked"
    machine.wait_until_succeeds("test -f /run/nixos-upgrade-first.status", timeout=20)
    machine.wait_until_succeeds("test -f /run/nixos-upgrade-second.status", timeout=20)
    first_result = json.loads(machine.succeed("cat /run/nixos-upgrade-first.out").strip())
    second_result = json.loads(machine.succeed("cat /run/nixos-upgrade-second.out").strip())
    assert first_result["system"] == "switched", first_result
    assert second_result["system"] == "stale", second_result

    # A user without host authorization cannot invoke the helper.
    profile_before_denied = machine.succeed("readlink -e " + shlex.quote(profile)).strip()
    denied = activation_command(candidate, user="denied-upgrader", no_commit=True)
    denied_status, _ = machine.execute(denied)
    assert denied_status != 0
    assert machine.succeed("readlink -e " + shlex.quote(profile)).strip() == profile_before_denied

    # Run the packaged app end-to-end as an unprivileged user. Build and diff
    # happen before the helper, and the lock is still old while switching.
    machine.succeed("git -C /etc/nixos reset --hard HEAD")
    reset_switch_markers()
    workflow_lock_before = machine.succeed("cat /etc/nixos/flake.lock")
    machine.succeed("printf 'workflow source change\\n' >> /etc/nixos/flake.nix")
    status, output = execute_app(
        helper_user, "--assume-yes", "--color=never"
    )
    assert status == 0, output
    assert "system=switched, lock=published, commit=committed" in output, output
    events = machine.succeed("cat /run/nixos-upgrade-test-events").splitlines()
    assert events == [
        "nix-update:" + upgrader_uid,
        "nix-build:" + upgrader_uid,
        "nvd:" + upgrader_uid,
        "profile-set",
        "switch-start",
        "switch-complete",
        "commit",
    ], events
    assert machine.succeed(
        "cat /run/nixos-upgrade-test-lock-during-switch"
    ) == workflow_lock_before
    assert machine.succeed("cat /etc/nixos/flake.lock").strip() == "user workflow lock"
    assert machine.succeed("cat /run/nixos-upgrade-test-commit-uid").strip() == "0"
    assert machine.succeed("cat /run/nixos-upgrade-test-commit-home").strip() == "/root"
    assert machine.succeed("git -C /etc/nixos log -1 --format=%an").strip() == "NixOS Repository Owner"
    assert profile_target() == candidate
    assert machine.succeed("git -C /etc/nixos status --porcelain").strip() == ""

    # Without host authorization, sudo fails noninteractively and the
    # system profile and repository remain unchanged.
    reset_switch_markers()
    profile_before_app_denial = profile_target()
    lock_before_app_denial = machine.succeed("cat /etc/nixos/flake.lock")
    head_before_app_denial = machine.succeed("git -C /etc/nixos rev-parse HEAD").strip()
    denied_uid = machine.succeed("id -u denied-upgrader").strip()
    denied_output_file, _, denied_status_file = launch_app_background(
        "denied-workflow",
        "denied-upgrader",
        "--assume-yes",
        "--no-commit",
        "--color=never",
    )
    machine.wait_until_succeeds(
        "test -f " + shlex.quote(denied_status_file), timeout=20
    )
    status = int(machine.succeed("cat " + shlex.quote(denied_status_file)).strip())
    output = machine.succeed("cat " + shlex.quote(denied_output_file))
    assert status != 0, output
    assert machine.succeed("cat /run/nixos-upgrade-test-events").splitlines() == [
        "nix-update:" + denied_uid,
        "nix-build:" + denied_uid,
        "nvd:" + denied_uid,
    ]
    assert profile_target() == profile_before_app_denial
    assert machine.succeed("cat /etc/nixos/flake.lock") == lock_before_app_denial
    assert machine.succeed("git -C /etc/nixos rev-parse HEAD").strip() == head_before_app_denial

    # --no-update-lock-file reuses the committed lock and --no-commit leaves
    # tracked changes untouched by Git commit.
    reset_switch_markers()
    machine.succeed("printf 'leave this change uncommitted\\n' >> /etc/nixos/flake.nix")
    lock_before_no_update = machine.succeed("cat /etc/nixos/flake.lock")
    commit_count_before_no_commit = machine.succeed(
        "git -C /etc/nixos rev-list --count HEAD"
    ).strip()
    status, output = execute_app(
        helper_user,
        "--no-update-lock-file",
        "--assume-yes",
        "--no-commit",
        "--color=never",
    )
    assert status == 0, output
    assert "lock=not-requested, commit=not-requested" in output, output
    assert machine.succeed("cat /run/nixos-upgrade-test-events").splitlines() == [
        "nix-build:" + upgrader_uid,
        "nvd:" + upgrader_uid,
        "profile-set",
        "switch-start",
        "switch-complete",
    ]
    assert machine.succeed("cat /run/nixos-upgrade-test-reference-lock") == lock_before_no_update
    assert machine.succeed("cat /etc/nixos/flake.lock") == lock_before_no_update
    assert machine.succeed(
        "git -C /etc/nixos rev-list --count HEAD"
    ).strip() == commit_count_before_no_commit
    assert machine.succeed(
        "git -C /etc/nixos diff --quiet && echo clean || echo dirty"
    ).strip() == "dirty"

    # A signal during the user-side build terminates and reaps its dedicated
    # process group without reaching sudo or changing the profile/repository.
    reset_switch_markers()
    profile_before_build_signal = profile_target()
    lock_before_build_signal = machine.succeed("cat /etc/nixos/flake.lock")
    machine.succeed("touch /run/nixos-upgrade-test-slow-build")
    build_output, build_pgid_file, build_status_file = launch_app_background(
        "build-signal",
        helper_user,
        "--assume-yes",
        "--no-commit",
        "--color=never",
    )
    machine.wait_until_succeeds(
        "test -s /run/nixos-upgrade-test-build-pid && "
        "test -s /run/nixos-upgrade-test-controller-pid && "
        "test -s /run/nixos-upgrade-test-build-child-pid",
        timeout=20,
    )
    build_pid = machine.succeed("cat /run/nixos-upgrade-test-build-pid").strip()
    controller_pid = machine.succeed("cat /run/nixos-upgrade-test-controller-pid").strip()
    build_child_pid = machine.succeed("cat /run/nixos-upgrade-test-build-child-pid").strip()
    machine.succeed("kill -TERM " + controller_pid)
    machine.wait_until_succeeds("test -f " + shlex.quote(build_status_file), timeout=20)
    assert int(machine.succeed("cat " + shlex.quote(build_status_file)).strip()) == 143
    build_signal_output = machine.succeed("cat " + shlex.quote(build_output))
    assert "'Terminated' signal received" in build_signal_output, build_signal_output
    assert machine.succeed("cat /run/nixos-upgrade-test-events").splitlines() == [
        "nix-update:" + upgrader_uid,
        "nix-build:" + upgrader_uid,
    ]
    assert profile_target() == profile_before_build_signal
    assert machine.succeed("cat /etc/nixos/flake.lock") == lock_before_build_signal
    machine.wait_until_succeeds("test ! -e /proc/" + build_pid, timeout=20)
    machine.wait_until_succeeds("test ! -e /proc/" + build_child_pid, timeout=20)
    machine.succeed("rm -f /run/nixos-upgrade-test-slow-build")

    # env --ignore-signal keeps supported signals ignored in the helper and
    # switch even though sudo resets its own dispositions. Python reports the
    # activation result before processing its queued SIGINT and exiting with 130.
    reset_switch_markers()
    lock_before_activation_signal = machine.succeed("cat /etc/nixos/flake.lock")
    machine.succeed("touch /run/nixos-upgrade-test-slow-activation")
    activation_output, activation_pgid_file, activation_status_file = launch_app_background(
        "activation-signal",
        helper_user,
        "--assume-yes",
        "--no-commit",
        "--color=never",
    )
    machine.wait_until_succeeds(
        "test -e /run/nixos-upgrade-test-switch-started && "
        "test -s /run/nixos-upgrade-test-signal-processes",
        timeout=20,
    )
    signal_processes = [
        line.split(":")
        for line in machine.succeed(
            "cat /run/nixos-upgrade-test-signal-processes"
        ).splitlines()
    ]
    activation_pgid = machine.succeed(
        "cat " + shlex.quote(activation_pgid_file)
    ).strip()
    python_process = next(
        item for item in signal_processes if item[1].startswith("python")
    )
    assert python_process[4] == activation_pgid, (python_process, activation_pgid)
    for process_name in ("sudo", "nixos-upgrade-a", "switch-to-confi"):
        process = next(
            item for item in signal_processes if item[1] == process_name
        )
        assert process[4] == activation_pgid, (process_name, process, activation_pgid)
        if process_name != "sudo":
            ignored_signals = int(process[3], 16)
            assert ignored_signals & (1 << (2 - 1)), (process_name, process)
            assert ignored_signals & (1 << (15 - 1)), (process_name, process)
    machine.succeed("kill -INT -- -" + activation_pgid)
    machine.wait_until_succeeds(
        "test -f " + shlex.quote(activation_status_file), timeout=20
    )
    activation_status = int(
        machine.succeed("cat " + shlex.quote(activation_status_file)).strip()
    )
    activation_signal_output = machine.succeed("cat " + shlex.quote(activation_output))
    assert activation_status == 130, activation_signal_output
    activation_result_index = activation_signal_output.find(
        "activation result: system=switched, lock=published, commit=not-requested"
    )
    signal_result_index = activation_signal_output.find("signal received")
    assert activation_result_index >= 0, activation_signal_output
    assert signal_result_index > activation_result_index, activation_signal_output
    assert "switch-complete" in machine.succeed("cat /run/nixos-upgrade-test-events")
    assert machine.succeed(
        "cat /run/nixos-upgrade-test-lock-during-switch"
    ) == lock_before_activation_signal
    assert machine.succeed("cat /etc/nixos/flake.lock").strip() == "user workflow lock"
    assert machine.succeed("cat /run/nixos-upgrade-test-events").splitlines() == [
        "nix-update:" + upgrader_uid,
        "nix-build:" + upgrader_uid,
        "nvd:" + upgrader_uid,
        "profile-set",
        "switch-start",
        "switch-complete",
    ]
    machine.succeed("rm -f /run/nixos-upgrade-test-slow-activation")

    # Restore the profile link that the VM booted with; this test never switches
    # the actual VM system closure.
    machine.succeed(
        "ln -sfnT -- " + shlex.quote(original_system) + " " + shlex.quote(profile)
    )
  '';
}
