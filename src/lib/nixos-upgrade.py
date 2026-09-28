import signal
import typing
import sys
import os
import logging
import argparse
import re
import types
import subprocess
import pathlib
import socket
import enum
import time
import shutil
import tempfile

import activation
import synsignals
import colorformatter
import nvd

import yaspin
import yaspin.spinners


class ColorOption(enum.StrEnum):
    AUTO = enum.auto()
    ALWAYS = enum.auto()
    NEVER = enum.auto()


class CliProgram:
    NAME = os.environ["NAME"]
    NIXOS_FLAKE_DEFAULT_PATH = "/etc/nixos/"
    FLAKE_LOCK = "flake.lock"
    STDIN_IS_A_TTY = os.isatty(sys.__stdin__.fileno())
    STDOUT_IS_A_TTY = os.isatty(sys.__stdout__.fileno())
    STDERR_IS_A_TTY = os.isatty(sys.__stderr__.fileno())
    HOSTNAME = socket.gethostname()
    NIXOS_CONFIG_FLAKE_OUT = \
        f"nixosConfigurations.{HOSTNAME}.config.system.build.toplevel"
    EXIT_ERR_CODE = 1
    EXIT_SIG_CODE_SHIFT = 128
    POLLING_PROC_SECS = 0.1
    SIG_TO_TERM_SUBPROC = signal.SIGTERM
    SIG_TO_KILL_SUBPROC = signal.SIGKILL
    TERM_SUBPROC_TIMEOUT = 5
    NO_COLOR_ENV_NAME = "NO_COLOR"
    NIX_EXTRA_EXPERIMENTAL_FEATURES = [
        "--extra-experimental-features",
        "nix-command flakes",
    ]

    def __init__(self):
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix=f"{self.NAME}-"
        )
        self.temporary_path = pathlib.Path(self.temporary_directory.name)
        self.lock_file_path = self.temporary_path / self.FLAKE_LOCK
        self.result_link = self.temporary_path / "result"
        self.running_subproc = None
        self.args = self.parse_args()
        self.logger = self.get_logger()

        # Arg errors after logger for fancy error messages
        if self.args._error is not None:
            self.exit_with_usage_error(
                str(self.args._error),
            )
        if self.args._argv is not None:
            self.exit_with_usage_error(
                "unrecognized arguments: " + ' '.join(self.args._argv),
            )

        self.spinner = self.get_spinner()
        self.current_system_closure = self.get_current_system_closure()
        self.upgraded_system_closure = None
        self.diff = None
        self.setup_signals()
        self.setup_excepthook()


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
                  "  SIGKILL has been sent to the subprocess as a last resort")

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

    def run_cmd(self, cmd: typing.List[str], desc="", msg_on_success="",
                *,
                stderr_out=False, with_spinner=True, exit_on_error=True,
                msg_on_success_loglevel=logging.INFO,
                env_to_update: dict = {},
                **kwargs) -> subprocess.CompletedProcess[str]:
        stderr_out = self.debug_mode or stderr_out

        if desc:
            self.logger.info(desc)

        command = " ".join(cmd)

        self.logger.debug("> " + command)

        if not stderr_out and with_spinner:
            self.spinner_start()
        elif stderr_out and with_spinner and not self.STDERR_IS_A_TTY:
            self.spinner_start()

        no_color = not self.colored_stderr

        env = os.environ.copy()

        if no_color:
            env[self.NO_COLOR_ENV_NAME] = "1"

        if env_to_update:
            env.update(env_to_update)

        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.STDOUT if not stderr_out else None,
            stdout=subprocess.PIPE,
            env=env,
            start_new_session=True,
            text=True,
            **kwargs
        )

        self.running_subproc = proc

        os.set_blocking(proc.stdout.fileno(), False)

        stdout_data = ""
        while proc.poll() is None:
            # While a subprocess is running
            # it's possible that a signal is received
            synsignals.handle()

            while line := proc.stdout.readline():
                if no_color:
                    line = self.clear_color(line)
                stdout_data += line
                if self.debug_mode:
                    sys.stderr.write(line)

            # So as not to be intrusive
            time.sleep(self.POLLING_PROC_SECS)

        self.running_subproc = None

        self.spinner_stop()

        tail = proc.stdout.read()
        if no_color:
            tail = self.clear_color(tail)
        stdout_data += tail

        if tail and self.debug_mode:
            sys.stderr.write(tail)

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
    def clear_color(text):
        termcolor_regex = r'\033\[[0-9;]+m'
        return re.sub(termcolor_regex, '', text)

    def parse_args(self):
        parser = argparse.ArgumentParser(
            prog=self.NAME,
            description="Updates nixos flake and shows changed packages",
            exit_on_error=False
        )

        parser.add_argument('--flake',
                            help=f"Nixos flake dir \
                                (default: {self.NIXOS_FLAKE_DEFAULT_PATH})",
                            default=self.NIXOS_FLAKE_DEFAULT_PATH,
                            type=pathlib.Path)

        parser.add_argument('-u', '--no-update-lock-file', action='store_true',
                            help=f"do not update {self.FLAKE_LOCK}")

        parser.add_argument('-m', '--commit-message',
                            help="add a commit message",
                            default="",
                            type=str)

        assume_group = parser.add_mutually_exclusive_group()
        assume_group.add_argument(
            '-y', '--assume-yes', action='store_true',
            help=('when a yes/no prompt would be presented, '
                  'assume that the user entered "yes". '
                  'In particular, suppresses the prompt that '
                  'appears when upgrading system.'))

        assume_group.add_argument(
            '-n', '--assume-no', action='store_true',
            help='likewise --assume-yes')

        parser.add_argument('-c', '--no-commit', action='store_true',
                            help='do not commit a flake repo')

        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="increase verbosity")

        parser.add_argument('-q', '--quiet', action='count', default=0,
                            help="decrease verbosity")

        parser.add_argument('--color',
                            choices=[ColorOption.AUTO.value,
                                     ColorOption.ALWAYS.value,
                                     ColorOption.NEVER.value],
                            default=ColorOption.AUTO,
                            help="when to display output using colors")

        args = types.SimpleNamespace()

        try:
            args, argv = parser.parse_known_args()
            if argv:
                args._argv = argv
            else:
                args._argv = None

            args._error = None

            args.verbosity = args.verbose - args.quiet

            if args.color == ColorOption.AUTO:
                args.colored_stdout, args.colored_stderr = \
                    self.is_output_colored()
            elif args.color == ColorOption.ALWAYS:
                args.colored_stdout, args.colored_stderr = True, True
            elif args.color == ColorOption.NEVER:
                args.colored_stdout, args.colored_stderr = False, False
        except argparse.ArgumentError as e:
            args._error = e
            args.verbosity = 0
            args.colored_stdout, args.colored_stderr = self.is_output_colored()

        return args

    @property
    def colored_stdout(self):
        return self.args.colored_stdout

    @property
    def colored_stderr(self):
        return self.args.colored_stderr

    # returns -> (stdout_colored: bool, stderr_colored: bool)
    def is_output_colored(self) -> (bool, bool):
        if "FORCE_COLOR" in os.environ:
            return (True, True)

        if (
            self.NO_COLOR_ENV_NAME in os.environ or
            "ANSI_COLORS_DISABLED" in os.environ or
            os.environ.get("TERM") == "dumb"
        ):
            return (False, False)

        return (self.STDOUT_IS_A_TTY, self.STDERR_IS_A_TTY)

    def get_formatter(self):
        return colorformatter.ColorFormatter(
            self.get_fmt_str(), color=self.colored_stderr)

    def get_fmt_str(self):
        return colorformatter.ColorFormatter.COLOR_FORMAT

    def get_logger(self) -> logging.Logger:
        # Handler.handleError() resolves raiseExceptions in the logging module.
        # https://docs.python.org/3/howto/logging.html#exceptions-raised-during-logging
        logging.raiseExceptions = __debug__

        stderr_handler = logging.StreamHandler()
        stderr_handler.setFormatter(self.get_formatter())

        logger = logging.getLogger(self.NAME)
        logger.addHandler(stderr_handler)

        self.config_verbosity(logger)

        # Optimize unnecessary things
        # See https://docs.python.org/3/howto/logging.html#optimization
        logging._srcfile = None
        logging.logThreads = False
        logging.logProcesses = False
        logging.logMultiprocessing = False

        return logger

    def get_spinner(self):
        if self.STDERR_IS_A_TTY and os.environ.get("TERM") != "dumb":
            color = "green" if self.colored_stderr else None
            return yaspin.yaspin(
                yaspin.spinners.Spinners.point,
                color=color,
                stream=sys.stderr,
            )

    def spinner_start(self, color="green"):
        if self.has_spinner:
            self.spinner.color = color if self.colored_stderr else None
            self.spinner.start()

    def spinner_stop(self):
        if self.has_spinner:
            self.spinner.stop()

    def config_verbosity(self, logger):
        match self.args.verbosity:
            case _ if self.args.verbosity <= -1:
                logger.setLevel(logging.ERROR)
            case 0:
                logger.setLevel(logging.WARNING)
            case 1:
                logger.setLevel(logging.INFO)
            case _:
                logger.setLevel(logging.DEBUG)

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
        self.exit_with_error(
            msg,
            os.EX_USAGE
        )

    def exit_with_error(self, msg=None, code=EXIT_ERR_CODE) -> typing.NoReturn:
        assert code != os.EX_OK
        self.exit(code, msg, logging.ERROR)

    def exit_with_signal(self, signum: int) -> typing.NoReturn:
        exit_code = self.get_sig_exit_code(signum)
        msg = self.get_sig_received_msg(signum)

        self.exit_with_error(msg, exit_code)

    def exit_with_success(self, msg=None) -> typing.NoReturn:
        self.exit(os.EX_OK, msg, None)

    def exit(self, code: int, msg=None, level=logging.INFO) -> typing.NoReturn:
        self.spinner_stop()

        if msg and level:
            self.logger.log(level, msg)
        elif msg:
            print(msg)

        sys.exit(code)

    @property
    def has_spinner(self) -> bool:
        if hasattr(self, "spinner") and self.spinner:
            return True

        return False

    @property
    def debug_mode(self) -> bool:
        return (self.logger.level == logging.DEBUG)

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
            "--flake",
            str(self.args.flake),
            "--output-lock-file",
            str(self.lock_file_path),
        ]
        update = self.run_cmd(
            command,
            "updating flake lock file...",
            exit_on_error=False,
        )

        if update.returncode != 0 or not self.lock_file_path.is_file():
            self.exit_with_error("updating lock file error", update.returncode or self.EXIT_ERR_CODE)

    @synsignals.add_handling
    def build_nixos_system(self):
        nixos_config = (f"{self.args.flake}#"
                        f"{self.NIXOS_CONFIG_FLAKE_OUT}")
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
            command.extend([
                "--reference-lock-file",
                str(self.lock_file_path),
            ])
        command.append(nixos_config)

        build = self.run_cmd(
            command,
            "building nixos system...",
            exit_on_error=False,
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
        if (
            self.current_system_closure ==
            self.upgraded_system_closure
        ):
            self.exit_with_success("no changes found")

        diff = self.run_cmd(
            ["nvd", "--color=always", "diff",
                str(self.current_system_closure),
                str(self.upgraded_system_closure)],
            "Comparing derivations...",
        )
        self.diff = diff.stdout

        if self.has_pkgs_changes():
            self.logger.warning("package changes found")
        else:
            self.logger.warning("config changes found")

    @synsignals.add_handling
    def print_updates(self):
        self.diff = nvd.format_diff(self.diff)
        print(self.diff)

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

        commit_message = (
            None if self.args.no_commit else self.get_commit_msg()
        )
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
        ANSWER_NO = 'n'
        ANSWER_YES = 'y'

        prompt = (self.get_changes_stat_str() + ". " +
                  f"Upgrade system? ([{ANSWER_NO}]/{ANSWER_YES}): ")

        assume_no = self.args.assume_no
        assume_yes = self.args.assume_yes
        assume_answer = assume_no or assume_yes

        if self.STDIN_IS_A_TTY and not assume_answer:
            if self.STDOUT_IS_A_TTY:
                sys.stdout.write(prompt)
            elif self.STDERR_IS_A_TTY:
                sys.stderr.write(prompt)

        if assume_no:
            answer = ANSWER_NO
        elif assume_yes:
            answer = ANSWER_YES
        else:
            try:
                answer = input()
            except EOFError:
                answer = ANSWER_NO

        self.logger.warning(prompt + answer)

        if answer.upper() == 'Y':
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

        if not self.STDOUT_IS_A_TTY:
            os.dup2(devnull, sys.stdout.fileno())

        if not self.STDERR_IS_A_TTY:
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
