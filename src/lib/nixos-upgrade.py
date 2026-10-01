import signal
import typing
import sys
import os
import logging
import types

import pathlib
import socket
import fcntl
import shutil
import tempfile

import activation
import synsignals
import nvd
import cli_options
import command_runner
import console as console_module
import nix_workflow


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
    EXIT_ERR_CODE = 1
    EXIT_SIG_CODE_SHIFT = 128
    POLLING_PROC_SECS = 0.1
    TERM_SUBPROC_TIMEOUT = 5

    def __init__(self):
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

        self.runner = command_runner.CommandRunner(
            self.console,
            poll_interval=self.POLLING_PROC_SECS,
            terminate_timeout=self.TERM_SUBPROC_TIMEOUT,
        )
        self.singleton_lock_fd = None
        self.check_singleton()

        self.temporary_directory = tempfile.TemporaryDirectory(prefix=f"{self.NAME}-")
        self.temporary_path = pathlib.Path(self.temporary_directory.name)
        self.workspace = nix_workflow.NixWorkspace(
            lock_file_path=self.temporary_path / "flake.lock",
            result_link=self.temporary_path / "result",
        )
        self.spinner = self.get_spinner()
        self.current_system_closure = self.get_current_system_closure()
        self.nix_workflow = nix_workflow.NixWorkflow(
            options=self.options,
            runner=self.runner,
            workspace=self.workspace,
            current_system_closure=self.current_system_closure,
        )
        self.upgraded_system_closure = None
        self.diff = ""
        self.changes = nvd.ChangeCounts()
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
        self.runner.terminate_active()
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

    def get_changes_stat_str(self):
        return nvd.format_change_summary(self.changes)

    @synsignals.add_handling
    def validate_flake(self):
        try:
            flake_dir = self.nix_workflow.validate_flake()
        except nix_workflow.NixWorkflowError as error:
            self.exit_with_error(error.message, error.exit_code)

        self.args.flake = flake_dir
        self.logger.info(f"found a nixos flake '{flake_dir}'")

    @synsignals.add_handling
    def prepare_upgrade(self):
        try:
            outcome = self.nix_workflow.prepare(self.args.flake)
        except nix_workflow.NixWorkflowError as error:
            self.exit_with_error(error.message, error.exit_code)

        if isinstance(outcome, nix_workflow.NoChanges):
            self.exit_with_success("no changes found")

        self.upgraded_system_closure = outcome.upgraded_system_closure
        self.diff = outcome.diff
        self.changes = outcome.changes
        if self.changes.total > 0:
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
                self.workspace.lock_file_path.read_bytes()
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
                completed = self.runner.run(
                    command,
                    output_policy=command_runner.OutputPolicy.INHERIT_STDERR,
                    with_spinner=False,
                    stdin_data=manifest,
                    start_new_session=False,
                    log_command=False,
                    log_failure=False,
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
            self.validate_flake()
            self.prepare_upgrade()
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
