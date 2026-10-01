import logging
import os
import re
import sys
from collections.abc import Mapping
from typing import TextIO

import cli_options
import colorformatter
import yaspin
import yaspin.spinners


NO_COLOR_ENV_NAME = "NO_COLOR"
_COLOR_SEQUENCE = re.compile(r"\033\[[0-9;]+m")


def stream_is_tty(stream: TextIO | None) -> bool:
    if stream is None:
        return False

    try:
        return stream.isatty()
    except (AttributeError, OSError, ValueError):
        return False


class Console:
    def __init__(
        self,
        options: cli_options.Options,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ):
        self.options = options
        self.stdin = sys.stdin if stdin is None else stdin
        self.stdout = sys.stdout if stdout is None else stdout
        self.stderr = sys.stderr if stderr is None else stderr
        self.stdin_is_tty = stream_is_tty(self.stdin)
        self.stdout_is_tty = stream_is_tty(self.stdout)
        self.stderr_is_tty = stream_is_tty(self.stderr)
        self.colored_stdout, self.colored_stderr = self._output_colors()
        self.logger = self._configure_logger()
        self.spinner = self._make_spinner()
        self._spinner_started = False

    def _output_colors(self) -> tuple[bool, bool]:
        match self.options.color:
            case cli_options.ColorOption.ALWAYS:
                return True, True
            case cli_options.ColorOption.NEVER:
                return False, False

        if "FORCE_COLOR" in os.environ:
            return True, True

        if (
            NO_COLOR_ENV_NAME in os.environ
            or "ANSI_COLORS_DISABLED" in os.environ
            or os.environ.get("TERM") == "dumb"
        ):
            return False, False

        return self.stdout_is_tty, self.stderr_is_tty

    @property
    def debug_mode(self) -> bool:
        return self.logger.level == logging.DEBUG

    def _configure_logger(self) -> logging.Logger:
        # Handler.handleError() resolves raiseExceptions in the logging module.
        # https://docs.python.org/3/howto/logging.html#exceptions-raised-during-logging
        logging.raiseExceptions = __debug__

        stderr_handler = logging.StreamHandler(self.stderr)
        stderr_handler.setFormatter(
            colorformatter.ColorFormatter(
                colorformatter.ColorFormatter.COLOR_FORMAT,
                color=self.colored_stderr,
            )
        )

        logger = logging.getLogger(os.environ["NAME"])
        logger.addHandler(stderr_handler)

        match self.options.verbosity:
            case _ if self.options.verbosity <= -1:
                logger.setLevel(logging.ERROR)
            case 0:
                logger.setLevel(logging.WARNING)
            case 1:
                logger.setLevel(logging.INFO)
            case _:
                logger.setLevel(logging.DEBUG)

        logging._srcfile = None
        logging.logThreads = False
        logging.logProcesses = False
        logging.logMultiprocessing = False

        return logger

    def _make_spinner(self):
        if self.stderr_is_tty and os.environ.get("TERM") != "dumb":
            color = "green" if self.colored_stderr else None
            return yaspin.yaspin(
                yaspin.spinners.Spinners.point,
                color=color,
                stream=self.stderr,
            )

        return None

    def start_spinner(self, color: str = "green") -> None:
        if self.spinner is not None:
            if self.colored_stderr:
                self.spinner.color = color
            self.spinner.start()
            self._spinner_started = True

    def stop_spinner(self) -> None:
        if self.spinner is not None and self._spinner_started:
            self.spinner.stop()
            self._spinner_started = False

    def child_environment(self, base: Mapping[str, str]) -> dict[str, str]:
        environment = dict(base)
        if self.options.color is cli_options.ColorOption.ALWAYS:
            environment["FORCE_COLOR"] = "1"
        if (
            self.options.color is cli_options.ColorOption.NEVER
            or not self.colored_stderr
        ):
            environment[NO_COLOR_ENV_NAME] = "1"
        return environment

    def confirm(self, prompt: str) -> bool:
        if self.options.assume_no:
            answer = "n"
        elif self.options.assume_yes:
            answer = "y"
        else:
            if self.stdin_is_tty:
                if self.stdout_is_tty:
                    prompt_stream = self.stdout
                elif self.stderr_is_tty:
                    prompt_stream = self.stderr
                else:
                    prompt_stream = None

                if prompt_stream is not None:
                    prompt_stream.write(prompt)
                    prompt_stream.flush()

            try:
                answer = self.stdin.readline()
            except EOFError:
                answer = ""

            if answer == "":
                answer = "n"
            else:
                answer = answer.removesuffix("\n")

        self.logger.warning(prompt + answer)
        return answer.strip().casefold() in {"y", "yes"}

    def display_diff(self, text: str) -> None:
        print(text, file=self.stdout)

    @staticmethod
    def strip_color(text: str) -> str:
        return _COLOR_SEQUENCE.sub("", text)
