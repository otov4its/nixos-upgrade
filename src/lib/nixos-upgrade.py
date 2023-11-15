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
import atexit

import synsignals
import colorformatter

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
    PY_SH_FD = int(os.environ["PY_SH_FD"])
    SH_PY_FD = int(os.environ["SH_PY_FD"])
    COMMIT_MSG_W_FD = int(os.environ["COMMIT_MSG_W_FD"])
    IFS = os.environ["CMD_IFS"]
    TMP_DIR = pathlib.Path(os.environ["TMP_DIR"])

    def __init__(self):
        self.from_worker_file = os.fdopen(self.SH_PY_FD, "r")
        atexit.register(self.atexit_handler)
        self.running_subproc = None
        self._stdout = sys.stdout
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

    def atexit_handler(self):
        # To avoid BrokenPipe ignored exception at exit
        self.std_streams_to_devnull()
        self.write_to_pipe(['exit'])
        self.from_worker_file.close()

    def setup_excepthook(self):
        # Uncaught exceptions
        sys.excepthook = self.exception_handler

    def setup_signals(self):
        signals = {}

        for s in os.environ["TERM_CORE_SIGS"].split(" "):
            signals[int(s)] = self.termination_signal_handler

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

        # self.logger.critical(f"{exc_value}")

    def get_current_system_closure(self):
        current_system_closure = self.run_privileged_task(
            "get_current_system_closure")

        self.logger.debug(f"{current_system_closure=}")
        return current_system_closure

    def run_cmd(self, cmd: typing.List[str], desc="", msg_on_success="",
                *,
                stderr_out=False, with_spinner=True, exit_on_error=True,
                msg_on_success_loglevel=logging.INFO,
                env_to_update: dict = {},
                **kwargs):
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

            # TODO: pty; print oneliners; catch in bufsize=1 escape symbols

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
        return stdout_data

    @staticmethod
    def clear_color(text):
        termcolor_regex = r'\033\[[0-9;]+m'
        return re.sub(termcolor_regex, '', text)

    # def colored_output(command: str = ""):
    #     colored_out = run(f"script -q -c '{command}' /dev/null",
    #                       check=True,
    #                       capture_output=True,
    #                       shell=True,
    #                       text=True)
    #     return colored_out

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

        parser.add_argument('-y', '--assume-yes', action='store_true',
                            help=('when a yes/no prompt would be presented, '
                                  'assume that the user entered "yes". '
                                  'In particular, suppresses the prompt that '
                                  'appears when upgrading system.'))

        parser.add_argument('-n', '--assume-no', action='store_true',
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

        # if self.STDIN_IS_A_TTY and self.STDOUT_IS_A_TTY:
        #     return fmt
        # else:
        #     # For convenient identification of message emitter in pipes
        #     return "%(name)s: " + fmt

    # TODO: add logging to system logger
    def get_logger(self) -> logging.Logger:
        this_module = sys.modules[__name__]
        # https://docs.python.org/3/howto/logging.html#exceptions-raised-during-logging
        this_module.raiseExceptions = True if __debug__ else False

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
        if self.STDERR_IS_A_TTY and self.colored_stderr:
            # By default yaspin writes to stdout, but we need stderr
            sys.stdout = sys.stderr
        elif self.STDOUT_IS_A_TTY and self.colored_stdout:
            pass
        else:
            return None

        spinner = yaspin.yaspin(yaspin.spinners.Spinners.point,
                                color="green")
        sys.stdout = self._stdout

        return spinner

    def spinner_start(self, color="green"):
        if self.has_spinner:
            if self.STDERR_IS_A_TTY:
                # By default yaspin writes to stdout, but we need stderr
                sys.stdout = sys.stderr

            self.spinner.color = color
            self.spinner.start()

    def spinner_stop(self):
        if self.has_spinner:
            self.spinner.stop()

            # Restore stdout
            sys.stdout = self._stdout

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

    def is_flake_dir_exists(self):
        res = self.run_privileged_task("is_dir_flake_exists")

        return res == "OK"

    def is_flake_file_exists(self):
        res = self.run_privileged_task("is_flake_file_exists")

        return res == "OK"

    def run_privileged_task(self, name: str, *args: str):
        self.write_to_pipe_check([name, *args])

        task_result = self.readline_from_worker()

        return task_result

    def readline_from_worker(self):
        return self.from_worker_file.readline().rstrip()

    def get_nixos_flake_dir(self):
        flake_dir = self.args.flake
        self.logger.debug(f"{flake_dir=}")

        flake_dir = self.run_privileged_task(
            "resolve_flake_dir", str(flake_dir))

        self.logger.debug(f"  resolved to {flake_dir!r}")

        if not self.is_flake_dir_exists():
            self.exit_with_error(f"{flake_dir}: no such directory")
        self.logger.debug("  exists")

        if not self.is_flake_file_exists():
            self.exit_with_error(f"{flake_dir}: this dir is not a flake")
        self.logger.debug("  and it's a flake")

        cp_result = self.run_privileged_task("setup_tmp_dir")

        if cp_result != "OK":
            self.exit_with_error("copying flake dir problem")

        check_nixos_config = self.run_privileged_task("check_nixos_config")

        if check_nixos_config != "OK":
            self.exit_with_error(
                f"{flake_dir}: flake: nixosConfigurations not found")
        self.logger.debug("  with nixos configuration")

        return flake_dir

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

        # if code != os.EX_OK:
        #     self.logger.error("Exit")

        # if self.termination_signum:
        #     signal.signal(self.termination_signum, signal.SIG_DFL)
        #     signal.raise_signal(self.termination_signum)

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
        # Marker of updates in packages
        regex = r"\[.+\]"

        return True if re.search(regex, self.diff) else False

    def count_changes(self):
        diff = self.clear_color(self.diff)
        changes = types.SimpleNamespace()

        changes.added = len(re.findall(r"\[A.\]", diff))
        changes.removed = len(re.findall(r"\[R.\]", diff))
        changes.upgraded = len(re.findall(r"\[U.\]", diff))
        changes.downgraded = len(re.findall(r"\[D.\]", diff))
        changes.changed = len(re.findall(r"\[C.\]", diff))
        changes.all = (changes.added + changes.removed + changes.upgraded +
                       changes.downgraded + changes.changed)

        return changes

    def get_changes_stat_str(self):
        changes = self.count_changes()
        if changes.all == 0:
            return "Config changes found"

        stat = f"{changes.all} package changes: "

        if changes.added > 0:
            stat += f"{changes.added} added, "
        if changes.removed > 0:
            stat += f"{changes.removed} removed, "
        if changes.upgraded > 0:
            stat += f"{changes.upgraded} upgraded, "
        if changes.downgraded > 0:
            stat += f"{changes.downgraded} downgraded, "
        if changes.changed > 0:
            stat += f"{changes.changed} changed, "

        stat = stat[:-2]

        return stat

    def process_diff(self) -> str:
        self.diff = "\n".join(self.diff.split("\n")[2:])

    @synsignals.add_handling
    def check_flake_dir(self):
        self.args.flake = self.get_nixos_flake_dir()
        self.logger.info(f"found a nixos flake '{self.args.flake}'")

    @synsignals.add_handling
    def update_lock_file(self):
        update = self.run_privileged_task("update_lock_file")

        if update != "OK":
            self.exit_with_error("updating lock file error")

    @synsignals.add_handling
    def build_nixos_system(self):
        nixos_config = (f"{self.TMP_DIR}#"
                        f"{self.NIXOS_CONFIG_FLAKE_OUT}")
        self.logger.debug(f"{nixos_config=}")

        self.logger.info("building nixos system...")

        build = self.run_privileged_task("build", nixos_config)

        if build == "OK":
            self.logger.info("ok")
        else:
            self.exit_with_error("building nixos system subprocess error")

        self.upgraded_system_closure = self.readline_from_worker()

        self.logger.debug(f"{self.upgraded_system_closure=}")

    @synsignals.add_handling
    def diff_closures(self):
        if (
            self.current_system_closure ==
            self.upgraded_system_closure
        ):
            self.exit_with_success("no changes found")

        self.diff = self.run_cmd(
            ["nvd", "--color=always", "diff",
                str(self.current_system_closure),
                str(self.upgraded_system_closure)],
            "Comparing derivations...",
        )

        if self.has_pkgs_changes():
            self.logger.warning("package changes found")
        else:
            self.logger.warning("config changes found")

    @synsignals.add_handling
    def print_updates(self):
        self.process_diff()
        print(self.diff)
        # for line in self.diff.splitlines():
        #     # Hack with colored("") for reset color code before colored `line`
        #     self.logger.warning("  " + termcolor.colored("") + line)

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

            upgrade = self.run_privileged_task(
                "upgrade", str(self.upgraded_system_closure))

            # Commit flake repo
            if not self.args.no_commit and upgrade == "OK":
                # Write commit message
                with os.fdopen(self.COMMIT_MSG_W_FD, 'w') as f:
                    f.write(self.get_commit_msg())

                commit = self.run_privileged_task("commit")

                if commit == "OK":
                    self.logger.warning("flake repo committed")
                else:
                    self.logger.error("flake repo committing subprocess error")

            if upgrade == "OK":
                self.exit_with_success("system upgraded")
            else:
                self.exit_with_error("switching to upgraded system error")

        else:
            self.exit_with_success("nothing changed")

    def write_to_pipe_check(self, cmd: list[str]):
        self.write_to_pipe(cmd)

        os.set_blocking(self.SH_PY_FD, False)
        attempts = 100
        while attempts > 0:
            time.sleep(0.01)
            pong = self.readline_from_worker()
            if pong:
                break
            attempts -= 1
        os.set_blocking(self.SH_PY_FD, True)

        if pong != "PONG":
            self.exit_with_error("privileged process is not responding")

    def write_to_pipe(self, cmd: list[str]):
        cmd = self.IFS.join(cmd) + "\n"

        os.write(self.PY_SH_FD, cmd.encode())

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
