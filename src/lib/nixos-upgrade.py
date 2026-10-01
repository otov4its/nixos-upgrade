import signal
import typing
import sys
import os
import logging
import types
import subprocess
import pathlib
import socket
import fcntl
import time
import shutil
import tempfile

import activation
import synsignals
import nvd
import cli_options
import console as console_module


def stream_is_tty(stream: typing.TextIO | None) -> bool:
    return console_module.stream_is_tty(stream)


ColorOption = cli_options.ColorOption


def get_runtime_defaults() -> cli_options.RuntimeDefaults:
    return cli_options.RuntimeDefaults(
        name=os.environ["NAME"],
        version=os.environ.get("VERSION", "development"),
        hostname=socket.gethostname(),
        default_flake=pathlib.Path("/etc/nixos/"),
    )


class CliProgram:
    NIXOS_FLAKE_DEFAULT_PATH = "/etc/nixos/"
    FLAKE_LOCK = "flake.lock"

    EXIT_ERR_CODE = 1
    EXIT_SIG_CODE_SHIFT = 128
    POLLING_PROC_SECS = 0.1
    SIG_TO_TERM_SUBPROC = signal.SIGTERM
    SIG_TO_KILL_SUBPROC = signal.SIGKILL
    TERM_SUBPROC_TIMEOUT = 5

    NIX_EXTRA_EXPERIMENTAL_FEATURES = [
        "--extra-experimental-features",
        "nix-command flakes",
    ]

    def __init__(self):
        self.running_subproc = None
        self.runtime_defaults = get_runtime_defaults()
        self.NAME = self.runtime_defaults.name
        self.VERSION = self.runtime_defaults.version
        self.HOSTNAME = self.runtime_defaults.hostname
        self.args = self.parse_args()
        self.console = console_module.Console(self.options)
        self.args.colored_stdout = self.console.colored_stdout
        self.args.colored_stderr = self.console.colored_stderr
        self.logger = self.get_logger()

        # Arg errors after logger for fancy error messages
        if self.args._error is not None:
            self.exit_with_usage_error(
                str(self.args._error),
            )
        if self.args._argv is not None:
            self.exit_with_usage_error(
                "unrecognized arguments: " + " ".join(self.args._argv),
            )

        self.singleton_lock_fd = None
        self.check_singleton()

        self.temporary_directory = tempfile.TemporaryDirectory(prefix=f"{self.NAME}-")
        self.temporary_path = pathlib.Path(self.temporary_directory.name)
        self.lock_file_path = self.temporary_path / self.FLAKE_LOCK
        self.result_link = self.temporary_path / "result"
        self.spinner = self.get_spinner()
        self.current_system_closure = self.get_current_system_closure()
        self.upgraded_system_closure = None
        self.diff = ""
        self.setup_signals()
        self.setup_excepthook()

    def check_singleton(self):
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime_dir:
            self.exit_with_error("XDG_RUNTIME_DIR is not set")

        lock_file = pathlib.Path(runtime_dir) / "nixos-upgrade.lock"
        try:
            lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as error:
            self.exit_with_error(
                f"unable to open singleton lock '{lock_file}': {error}"
            )

        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            self.exit_with_error("process is already running")
        except OSError as error:
            os.close(lock_fd)
            self.exit_with_error(
                f"unable to lock singleton lock '{lock_file}': {error}"
            )

        self.singleton_lock_fd = lock_fd

    def setup_excepthook(self):
        # Uncaught exceptions
        sys.excepthook = self.exception_handler

    def setup_signals(self):
        signals = {}

        for s in os.environ["TERM_CORE_SIGS"].split():
            signals[int(s)] = self.termination_signal_handler

        self.supported_signals = tuple(signals)
        synsignals.set(signals)

        # Unblock all blocked signals
        signal.pthread_sigmask(signal.SIG_UNBLOCK, signal.valid_signals())

    def termination_signal_handler(self, signum, frame):
        self.spinner_stop()

        self.logger.error(self.get_sig_received_msg(signum))

        if self.running_subproc:
            proc = self.running_subproc

            if proc.returncode is None:
                self.logger.warning("terminating running subprocess...")
                os.killpg(proc.pid, self.SIG_TO_TERM_SUBPROC)

            self.spinner_start("yellow")

            try:
                # Waiting for termination
                proc.communicate(timeout=self.TERM_SUBPROC_TIMEOUT)
            except subprocess.TimeoutExpired:
                # Last resort
                os.killpg(proc.pid, self.SIG_TO_KILL_SUBPROC)

                self.spinner_stop()
                self.logger.warning(
                    "  SIGKILL has been sent to the subprocess as a last resort"
                )

                proc.communicate()

            self.spinner_stop()

            self.logger.warning("ok")

        self.exit_with_error(None, self.get_sig_exit_code(signum))

    def exception_handler(self, exc_type, exc_value, exc_traceback):
        self.spinner_stop()

        self.exit(self.EXIT_ERR_CODE, f"{exc_value}", logging.CRITICAL)

    def get_current_system_closure(self):
        current_system_closure = str(
            pathlib.Path("/run/current-system").resolve(strict=True)
        )

        self.logger.debug(f"{current_system_closure=}")
        return current_system_closure

    def run_cmd(
        self,
        cmd: typing.List[str],
        desc="",
        msg_on_success="",
        *,
        stderr_out=False,
        with_spinner=True,
        exit_on_error=True,
        msg_on_success_loglevel=logging.INFO,
        env_to_update: typing.Mapping[str, str] | None = None,
        **kwargs,
    ) -> subprocess.CompletedProcess[str]:
        stderr_out = self.debug_mode or stderr_out

        if desc:
            self.logger.info(desc)

        command = " ".join(cmd)

        self.logger.debug("> " + command)

        if not stderr_out and with_spinner:
            self.spinner_start()
        elif stderr_out and with_spinner and not self.console.stderr_is_tty:
            self.spinner_start()

        no_color = not self.colored_stderr

        env = self.console.child_environment(os.environ)

        if env_to_update is not None:
            env.update(env_to_update)

        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.STDOUT if not stderr_out else None,
            stdout=subprocess.PIPE,
            env=env,
            start_new_session=True,
            text=True,
            **kwargs,
        )

        self.running_subproc = proc

        stdout = proc.stdout
        if stdout is None:
            raise RuntimeError("subprocess stdout pipe was not created")

        os.set_blocking(stdout.fileno(), False)

        stdout_data = ""
        while proc.poll() is None:
            # While a subprocess is running
            # it's possible that a signal is received
            synsignals.handle()

            while line := stdout.readline():
                if no_color:
                    line = self.clear_color(line)
                stdout_data += line
                if self.debug_mode:
                    self.console.stderr.write(line)

            # So as not to be intrusive
            time.sleep(self.POLLING_PROC_SECS)

        self.running_subproc = None

        self.spinner_stop()

        tail = stdout.read()
        if no_color:
            tail = self.clear_color(tail)
        stdout_data += tail

        if tail and self.debug_mode:
            self.console.stderr.write(tail)

        retcode = proc.returncode

        if retcode != os.EX_OK:
            msg = f"`{command}` subprocess error"
            self.logger.error(msg)

            if exit_on_error:
                self.exit_with_error(code=retcode)
        else:
            if msg_on_success:
                self.logger.log(msg_on_success_loglevel, msg_on_success)

        self.logger.debug("> done")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=retcode,
            stdout=stdout_data,
            stderr=None,
        )

    @staticmethod
    def clear_color(text: str) -> str:
        return console_module.Console.strip_color(text)

    def parse_args(self):
        if not hasattr(self, "runtime_defaults"):
            self.runtime_defaults = get_runtime_defaults()

        usage_error = None
        try:
            options = cli_options.parse_args(sys.argv[1:], self.runtime_defaults)
        except cli_options.CliUsageError as error:
            options = error.options
            usage_error = error

        self.options = options
        args = types.SimpleNamespace(**options.__dict__)
        args._argv = None
        args._error = usage_error
        return args

    @property
    def colored_stdout(self):
        return self.console.colored_stdout

    @property
    def colored_stderr(self):
        return self.console.colored_stderr

    def get_logger(self) -> logging.Logger:
        return self.console.logger

    def get_spinner(self):
        return self.console.spinner

    def spinner_start(self, color: str = "green"):
        self.console.start_spinner(color)

    def spinner_stop(self):
        self.console.stop_spinner()

    @property
    def has_spinner(self) -> bool:
        return self.console.spinner is not None

    @property
    def debug_mode(self) -> bool:
        return self.console.debug_mode

    def get_nixos_flake_dir(self):
        flake_dir = self.args.flake
        self.logger.debug(f"{flake_dir=}")

        try:
            resolved_flake_dir = flake_dir.resolve(strict=True)
        except OSError:
            self.exit_with_error(f"{flake_dir}: no such directory")

        self.logger.debug(f"  resolved to {resolved_flake_dir!r}")
        if not resolved_flake_dir.is_dir():
            self.exit_with_error(f"{resolved_flake_dir}: no such directory")
        if not (resolved_flake_dir / "flake.nix").is_file():
            self.exit_with_error(f"{resolved_flake_dir}: this dir is not a flake")

        if self.args.no_update_lock_file:
            source_lock_file = resolved_flake_dir / self.FLAKE_LOCK
            if source_lock_file.is_file():
                try:
                    shutil.copyfile(source_lock_file, self.lock_file_path)
                except OSError as error:
                    self.exit_with_error(
                        f"preparing temporary lock file failed: {error}"
                    )

        return str(resolved_flake_dir)

    def get_sig_received_msg(self, signum: int):
        return f"'{signal.strsignal(signum)}' signal received"

    def get_sig_exit_code(self, signum: int):
        return self.EXIT_SIG_CODE_SHIFT + signum

    def exit_with_usage_error(self, msg=None):
        self.exit_with_error(msg, os.EX_USAGE)

    def exit_with_error(self, msg=None, code=EXIT_ERR_CODE) -> typing.NoReturn:
        assert code != os.EX_OK
        self.exit(code, msg, logging.ERROR)

    def exit_with_signal(self, signum: int) -> typing.NoReturn:
        exit_code = self.get_sig_exit_code(signum)
        msg = self.get_sig_received_msg(signum)

        self.exit_with_error(msg, exit_code)

    def exit_with_success(self, msg=None) -> typing.NoReturn:
        self.exit(os.EX_OK, msg, None)

    def exit(
        self, code: int, msg=None, level: int | None = logging.INFO
    ) -> typing.NoReturn:
        self.spinner_stop()

        if msg and level:
            self.logger.log(level, msg)
        elif msg:
            print(msg, file=self.console.stdout)

        sys.exit(code)

    def has_pkgs_changes(self) -> bool:
        return nvd.count_changes(self.diff).total > 0

    def get_changes_stat_str(self):
        return nvd.format_change_summary(nvd.count_changes(self.diff))

    @synsignals.add_handling
    def check_flake_dir(self):
        self.args.flake = self.get_nixos_flake_dir()
        self.logger.info(f"found a nixos flake '{self.args.flake}'")

    @synsignals.add_handling
    def update_lock_file(self):
        command = [
            "nix",
            *self.NIX_EXTRA_EXPERIMENTAL_FEATURES,
            "flake",
            "update",
        ]
        if self.args.inputs:
            command.extend(self.args.inputs)
        command.extend(
            [
                "--flake",
                str(self.args.flake),
                "--output-lock-file",
                str(self.lock_file_path),
            ]
        )
        update = self.run_cmd(
            command,
            "updating flake lock file...",
            exit_on_error=False,
            stderr_out=True,
        )

        if update.returncode != 0 or not self.lock_file_path.is_file():
            self.exit_with_error(
                "updating lock file error", update.returncode or self.EXIT_ERR_CODE
            )

    @synsignals.add_handling
    def build_nixos_system(self):
        nixos_config = (
            f"{self.args.flake}#nixosConfigurations."
            f"{self.args.configuration}.config.system.build.toplevel"
        )
        self.logger.debug(f"{nixos_config=}")

        command = [
            "nix",
            *self.NIX_EXTRA_EXPERIMENTAL_FEATURES,
            "build",
            "--out-link",
            str(self.result_link),
            "--no-write-lock-file",
        ]
        if self.lock_file_path.is_file():
            command.extend(
                [
                    "--reference-lock-file",
                    str(self.lock_file_path),
                ]
            )
        command.append(nixos_config)

        build = self.run_cmd(
            command,
            "building nixos system...",
            exit_on_error=False,
            stderr_out=True,
        )
        if build.returncode != 0:
            self.exit_with_error(
                "building nixos system subprocess error",
                build.returncode,
            )

        try:
            self.upgraded_system_closure = str(self.result_link.resolve(strict=True))
        except OSError:
            self.exit_with_error("could not resolve built system closure")

        self.logger.debug(f"{self.upgraded_system_closure=}")

    @synsignals.add_handling
    def diff_closures(self):
        if self.current_system_closure == self.upgraded_system_closure:
            self.exit_with_success("no changes found")

        diff = self.run_cmd(
            [
                "nvd",
                "--color=always",
                "diff",
                str(self.current_system_closure),
                str(self.upgraded_system_closure),
            ],
            "Comparing derivations...",
        )
        self.diff = diff.stdout or ""

        if self.has_pkgs_changes():
            self.logger.warning("package changes found")
        else:
            self.logger.warning("config changes found")

    @synsignals.add_handling
    def print_updates(self):
        self.diff = nvd.format_diff(self.diff)
        self.console.display_diff(self.diff)

    def report_activation_result(self, result: activation.ActivationResult):
        self.logger.warning(
            "activation result: system=%s, lock=%s, commit=%s",
            result.system,
            result.lock,
            result.commit,
        )

        if result.system != "switched":
            self.logger.error("system activation did not switch: %s", result.system)
            return
        if result.lock == "failed":
            self.logger.error(
                "system switched, but the updated flake.lock could not be published"
            )
        if result.commit == "failed":
            self.logger.error("system switched, but the repository commit failed")
        elif result.commit == "not-git":
            self.logger.error(
                "system switched, but the flake directory is not a Git repository"
            )

    def run_privileged_activation(self) -> activation.ActivationResult:
        try:
            lock_file_bytes = (
                self.lock_file_path.read_bytes()
                if not self.args.no_update_lock_file
                else None
            )
        except OSError as error:
            self.exit_with_error(f"could not read temporary lock file: {error}")

        commit_message = None if self.args.no_commit else self.get_commit_msg()
        env_path = shutil.which("env")
        if env_path is None:
            self.exit_with_error("GNU env is not available in the packaged PATH")

        request = activation.ActivationRequest(
            env_path=env_path,
            supported_signals=self.supported_signals,
            sudo_path=activation.DEFAULT_SUDO_PATH,
            helper_path=activation.HELPER_PATH,
            expected_current=str(self.current_system_closure),
            system_closure=str(self.upgraded_system_closure),
            flake_dir=str(self.args.flake),
            lock_file_bytes=lock_file_bytes,
            commit_message=commit_message,
            no_commit=self.args.no_commit,
        )
        command = activation.build_activation_command(request)
        manifest = activation.encode_activation_request(request)

        with synsignals.BlockedHandling():
            try:
                completed = subprocess.run(
                    command,
                    input=manifest,
                    stdout=subprocess.PIPE,
                    stderr=None,
                    text=True,
                    check=False,
                )
                result = activation.parse_activation_result(
                    completed.stdout,
                    completed.returncode,
                )
            except (OSError, ValueError) as error:
                self.logger.error("privileged activation request failed: %s", error)
                raise
            self.report_activation_result(result)

        return result

    @synsignals.add_handling
    def upgrade_system(self):
        ANSWER_NO = "n"
        ANSWER_YES = "y"

        prompt = (
            self.get_changes_stat_str()
            + ". "
            + f"Upgrade system? ([{ANSWER_NO}]/{ANSWER_YES}): "
        )

        if self.console.confirm(prompt):
            self.logger.info("switching to upgraded system...")
            result = self.run_privileged_activation()

            if result.system == "switched":
                self.exit_with_success("system upgraded")
            self.exit_with_error("switching to upgraded system error")

        self.exit_with_success("nothing changed")

    def get_commit_msg(self):
        header = f"{self.NAME}: Auto commit\n\n"

        user_msg = self.args.commit_message
        if user_msg:
            user_msg += "\n\n"

        updates = self.clear_color(self.diff)

        msg = header + user_msg + updates

        return msg

    def std_streams_to_devnull(self):
        devnull = os.open(os.devnull, os.O_WRONLY)

        if not self.console.stdout_is_tty:
            os.dup2(devnull, sys.stdout.fileno())

        if not self.console.stderr_is_tty:
            os.dup2(devnull, sys.stderr.fileno())

    def main(self):
        try:
            self.check_flake_dir()
            if not self.args.no_update_lock_file:
                self.update_lock_file()
            self.build_nixos_system()
            self.diff_closures()
            self.print_updates()
            self.upgrade_system()
        except BrokenPipeError:
            # Python flushes standard streams on exit;
            # redirect remaining output
            # to devnull to avoid another BrokenPipeError at shutdown.
            # See https://docs.python.org/3/library/signal.html#note-on-sigpipe
            self.std_streams_to_devnull()

            self.exit_with_signal(signal.SIGPIPE)


if __name__ == "__main__":
    CliProgram().main()
