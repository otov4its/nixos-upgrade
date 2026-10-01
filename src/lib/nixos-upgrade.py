import fcntl
import logging
import os
import pathlib
import shutil
import signal
import socket
import sys
import tempfile
from collections.abc import Sequence
from typing import Any

import activation
import cli_options
import command_runner
import console as console_module
import nix_workflow
import nvd
import synsignals


EXIT_ERR_CODE = 1
EXIT_SIG_CODE_SHIFT = 128
POLLING_PROC_SECS = 0.1
TERM_SUBPROC_TIMEOUT = 5
NIXOS_FLAKE_DEFAULT_PATH = pathlib.Path("/etc/nixos/")


def get_runtime_defaults() -> cli_options.RuntimeDefaults:
    return cli_options.RuntimeDefaults(
        name=os.environ["NAME"],
        version=os.environ.get("VERSION", "development"),
        hostname=socket.gethostname(),
        default_flake=NIXOS_FLAKE_DEFAULT_PATH,
    )


def get_current_system_closure() -> str:
    return str(pathlib.Path("/run/current-system").resolve(strict=True))


def acquire_singleton_lock(logger: logging.Logger) -> int | None:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        logger.error("XDG_RUNTIME_DIR is not set")
        return None

    lock_file = pathlib.Path(runtime_dir) / "nixos-upgrade.lock"
    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as error:
        logger.error("unable to open singleton lock '%s': %s", lock_file, error)
        return None

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        logger.error("process is already running")
        return None
    except OSError as error:
        os.close(lock_fd)
        logger.error("unable to lock singleton lock '%s': %s", lock_file, error)
        return None

    return lock_fd


class _SignalExit(Exception):
    def __init__(self, exit_code: int):
        super().__init__(exit_code)
        self.exit_code = exit_code


class _ControllerError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_ERR_CODE):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class CliProgram:
    def __init__(
        self,
        options: cli_options.Options,
        console: console_module.Console,
        runner: command_runner.CommandRunner,
        workflow: nix_workflow.NixWorkflow,
    ):
        self.options = options
        self.console = console
        self.logger = console.logger
        self.runner = runner
        self.workflow = workflow
        self.flake_dir: pathlib.Path | None = None
        self.upgraded_system_closure: str | None = None
        self.diff = ""
        self.changes = nvd.ChangeCounts()
        self.supported_signals: tuple[int, ...] = ()

    def setup_excepthook(self) -> None:
        sys.excepthook = self.exception_handler

    def setup_signals(self) -> None:
        signals: dict[int, Any] = {}
        for signum in os.environ["TERM_CORE_SIGS"].split():
            signals[int(signum)] = self.termination_signal_handler

        self.supported_signals = tuple(signals)
        synsignals.set(signals)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, signal.valid_signals())

    def termination_signal_handler(self, signum: int, frame: Any) -> None:
        self.console.stop_spinner()
        self.logger.error(self.get_sig_received_msg(signum))
        self.runner.terminate_active()
        raise _SignalExit(self.get_sig_exit_code(signum))

    def exception_handler(self, exc_type, exc_value, exc_traceback) -> None:
        self.console.stop_spinner()
        self.logger.critical(str(exc_value))

    @staticmethod
    def get_sig_received_msg(signum: int) -> str:
        return f"'{signal.strsignal(signum)}' signal received"

    @staticmethod
    def get_sig_exit_code(signum: int) -> int:
        return EXIT_SIG_CODE_SHIFT + signum

    def _write_success(self, message: str) -> int:
        print(message, file=self.console.stdout)
        return os.EX_OK

    def _change_summary(self) -> str:
        return nvd.format_change_summary(self.changes)

    def _handle_activation_result(self, result: activation.ActivationResult) -> int:
        if result.system == "switched":
            return self._write_success("system upgraded")

        self.logger.error("switching to upgraded system error")
        return EXIT_ERR_CODE

    def run(self) -> int:
        try:
            synsignals.handle()
            self.flake_dir = self.workflow.validate_flake()
            self.logger.info(f"found a nixos flake '{self.flake_dir}'")

            synsignals.handle()
            outcome = self.workflow.prepare(self.flake_dir)
            if isinstance(outcome, nix_workflow.NoChanges):
                return self._write_success("no changes found")

            self.upgraded_system_closure = outcome.upgraded_system_closure
            self.diff = nvd.format_diff(outcome.diff)
            self.changes = outcome.changes
            if self.changes.total > 0:
                self.logger.warning("package changes found")
            else:
                self.logger.warning("config changes found")

            synsignals.handle()
            self.console.display_diff(self.diff)
            prompt = self._change_summary() + ". Upgrade system? ([n]/y): "
            if not self.console.confirm(prompt):
                return self._write_success("nothing changed")

            self.logger.info("switching to upgraded system...")
            result = self.run_privileged_activation()
            return self._handle_activation_result(result)
        except nix_workflow.NixWorkflowError as error:
            self.console.stop_spinner()
            if error.message is not None:
                self.logger.error(error.message)
            return error.exit_code
        except _ControllerError as error:
            self.console.stop_spinner()
            self.logger.error(error.message)
            return error.exit_code
        except _SignalExit as exit_request:
            return exit_request.exit_code
        except BrokenPipeError:
            self.console.stop_spinner()
            self.std_streams_to_devnull()
            self.logger.error(self.get_sig_received_msg(signal.SIGPIPE))
            return self.get_sig_exit_code(signal.SIGPIPE)

    def report_activation_result(self, result: activation.ActivationResult) -> None:
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
                self.workflow.workspace.lock_file_path.read_bytes()
                if not self.options.no_update_lock_file
                else None
            )
        except OSError as error:
            raise _ControllerError(
                f"could not read temporary lock file: {error}"
            ) from error

        if self.flake_dir is None:
            raise RuntimeError("flake directory was not validated before activation")
        if self.upgraded_system_closure is None:
            raise RuntimeError("no upgraded system closure is available for activation")

        commit_message = None if self.options.no_commit else self.get_commit_msg()
        env_path = shutil.which("env")
        if env_path is None:
            raise _ControllerError("GNU env is not available in the packaged PATH")

        request = activation.ActivationRequest(
            env_path=env_path,
            supported_signals=self.supported_signals,
            sudo_path=activation.DEFAULT_SUDO_PATH,
            helper_path=activation.HELPER_PATH,
            expected_current=self.workflow.current_system_closure,
            system_closure=self.upgraded_system_closure,
            flake_dir=str(self.flake_dir),
            lock_file_bytes=lock_file_bytes,
            commit_message=commit_message,
            no_commit=self.options.no_commit,
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

    def get_commit_msg(self) -> str:
        header = f"{self.logger.name}: Auto commit\n\n"
        user_msg = self.options.commit_message
        if user_msg:
            user_msg += "\n\n"
        return header + user_msg + console_module.Console.strip_color(self.diff)

    def std_streams_to_devnull(self) -> None:
        devnull = os.open(os.devnull, os.O_WRONLY)
        if not self.console.stdout_is_tty:
            os.dup2(devnull, sys.stdout.fileno())
        if not self.console.stderr_is_tty:
            os.dup2(devnull, sys.stderr.fileno())


def main(argv: Sequence[str] | None = None) -> int:
    runtime_defaults = get_runtime_defaults()
    arguments = sys.argv[1:] if argv is None else argv

    try:
        options = cli_options.parse_args(arguments, runtime_defaults)
    except cli_options.CliUsageError as error:
        console = console_module.Console(error.options)
        console.logger.error(str(error))
        return os.EX_USAGE

    console = console_module.Console(options)
    lock_fd = acquire_singleton_lock(console.logger)
    if lock_fd is None:
        return EXIT_ERR_CODE

    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    try:
        try:
            temporary_directory = tempfile.TemporaryDirectory(
                prefix=f"{runtime_defaults.name}-"
            )
        except OSError as error:
            console.logger.critical(str(error))
            return EXIT_ERR_CODE

        try:
            current_system_closure = get_current_system_closure()
        except OSError as error:
            console.logger.critical(str(error))
            return EXIT_ERR_CODE
        console.logger.debug(f"{current_system_closure=}")

        runner = command_runner.CommandRunner(
            console,
            poll_interval=POLLING_PROC_SECS,
            terminate_timeout=TERM_SUBPROC_TIMEOUT,
        )
        temporary_path = pathlib.Path(temporary_directory.name)
        workspace = nix_workflow.NixWorkspace(
            lock_file_path=temporary_path / "flake.lock",
            result_link=temporary_path / "result",
        )
        workflow = nix_workflow.NixWorkflow(
            options=options,
            runner=runner,
            workspace=workspace,
            current_system_closure=current_system_closure,
        )
        program = CliProgram(options, console, runner, workflow)
        program.setup_signals()
        program.setup_excepthook()
        return program.run()
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
