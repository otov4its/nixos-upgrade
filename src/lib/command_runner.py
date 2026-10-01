from __future__ import annotations

import enum
import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from typing import IO, TYPE_CHECKING

import synsignals

if TYPE_CHECKING:
    from console import Console


class OutputPolicy(enum.StrEnum):
    MERGE_STDERR = enum.auto()
    INHERIT_STDERR = enum.auto()


class CommandRunner:
    def __init__(
        self,
        console: Console,
        *,
        poll_interval: float = 0.1,
        terminate_timeout: float = 5.0,
    ):
        self.console = console
        self.poll_interval = poll_interval
        self.terminate_timeout = terminate_timeout
        self._active_process: subprocess.Popen[str] | None = None

    def run(
        self,
        command: Sequence[str],
        *,
        description: str = "",
        output_policy: OutputPolicy = OutputPolicy.MERGE_STDERR,
        with_spinner: bool = True,
        env_updates: Mapping[str, str] | None = None,
        stdin_data: str | None = None,
        start_new_session: bool = True,
        log_command: bool = True,
        log_failure: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command_args = list(command)
        effective_policy = output_policy
        if self.console.debug_mode and effective_policy is OutputPolicy.MERGE_STDERR:
            effective_policy = OutputPolicy.INHERIT_STDERR

        command_text = " ".join(command_args)
        if log_command:
            if description:
                self.console.logger.info(description)
            self.console.logger.debug("> " + command_text)

        if with_spinner and (
            effective_policy is OutputPolicy.MERGE_STDERR
            or not self.console.stderr_is_tty
        ):
            self.console.start_spinner()

        environment = self.console.child_environment(os.environ)
        if env_updates is not None:
            environment.update(env_updates)

        no_color = not self.console.colored_stderr
        process = None
        try:
            process = subprocess.Popen(
                command_args,
                stdin=subprocess.PIPE if stdin_data is not None else None,
                stdout=subprocess.PIPE,
                stderr=(
                    subprocess.STDOUT
                    if effective_policy is OutputPolicy.MERGE_STDERR
                    else None
                ),
                env=environment,
                start_new_session=start_new_session,
                text=True,
            )
            self._active_process = process

            stdout = process.stdout
            if stdout is None:
                raise RuntimeError("subprocess stdout pipe was not created")

            if stdin_data is None:
                stdout_data = self._capture_output(stdout, process, no_color)
            else:
                output, _ = process.communicate(input=stdin_data)
                stdout_data = self._clean_output(output or "", no_color)
                self._echo_debug_output(stdout_data)

            returncode = process.wait()
        finally:
            self._active_process = None
            try:
                self.console.stop_spinner()
            finally:
                if process is not None:
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stdin is not None:
                        process.stdin.close()

        if returncode != os.EX_OK and log_failure:
            self.console.logger.error(f"`{command_text}` subprocess error")
        if log_command:
            self.console.logger.debug("> done")

        return subprocess.CompletedProcess(
            args=command_args,
            returncode=returncode,
            stdout=stdout_data,
            stderr=None,
        )

    def _capture_output(
        self,
        stdout: IO[str],
        process: subprocess.Popen[str],
        no_color: bool,
    ) -> str:
        os.set_blocking(stdout.fileno(), False)
        stdout_data = ""

        while process.poll() is None:
            synsignals.handle()

            while line := stdout.readline():
                line = self._clean_output(line, no_color)
                stdout_data += line
                self._echo_debug_output(line)

            time.sleep(self.poll_interval)

        tail = stdout.read()
        tail = self._clean_output(tail, no_color)
        stdout_data += tail
        self._echo_debug_output(tail)
        return stdout_data

    def _clean_output(self, text: str, no_color: bool) -> str:
        if no_color:
            return self.console.strip_color(text)
        return text

    def _echo_debug_output(self, text: str) -> None:
        if text and self.console.debug_mode:
            self.console.stderr.write(text)

    def terminate_active(self) -> None:
        process = self._active_process
        if process is None:
            return

        if process.poll() is None:
            self.console.logger.warning("terminating running subprocess...")
            os.killpg(process.pid, signal.SIGTERM)

        self.console.start_spinner("yellow")
        try:
            process.communicate(timeout=self.terminate_timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            self.console.stop_spinner()
            self.console.logger.warning(
                "  SIGKILL has been sent to the subprocess as a last resort"
            )
            process.communicate()

        self.console.stop_spinner()
        self.console.logger.warning("ok")
