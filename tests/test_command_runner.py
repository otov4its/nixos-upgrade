import io
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

LIBRARY_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "lib"
sys.path.insert(0, str(LIBRARY_DIR))

import command_runner  # noqa: E402


class FakeConsole:
    def __init__(self, *, debug_mode=False, stderr_is_tty=False, colored_stderr=True):
        self.debug_mode = debug_mode
        self.stderr_is_tty = stderr_is_tty
        self.colored_stderr = colored_stderr
        self.stderr = io.StringIO()
        self.child_environment_calls = 0
        self.logger = mock.Mock()
        self.start_spinner = mock.Mock()
        self.stop_spinner = mock.Mock()

    def child_environment(self, base):
        self.child_environment_calls += 1
        return {**base, "CONSOLE_VALUE": "console"}

    @staticmethod
    def strip_color(text):
        return re.sub(r"\033\[[0-9;]+m", "", text)


class CommandRunnerTests(unittest.TestCase):
    def make_runner(self, console=None, *, poll_interval=0.01, terminate_timeout=0.05):
        return command_runner.CommandRunner(
            console or FakeConsole(),
            poll_interval=poll_interval,
            terminate_timeout=terminate_timeout,
        )

    @staticmethod
    def python_command(script, *args):
        return [sys.executable, "-c", script, *args]

    def test_merge_policy_captures_stdout_and_stderr(self):
        runner = self.make_runner()
        script = (
            "import sys; "
            "print('from stdout', flush=True); "
            "print('from stderr', file=sys.stderr, flush=True)"
        )

        result = runner.run(self.python_command(script), with_spinner=False)

        self.assertEqual(result.returncode, 0)
        self.assertIn("from stdout\n", result.stdout)
        self.assertIn("from stderr\n", result.stdout)
        self.assertIsNone(result.stderr)

    def test_inherit_policy_keeps_stderr_inherited_and_separate(self):
        runner = self.make_runner()
        real_popen = subprocess.Popen
        popen_arguments = {}

        def record_popen(*args, **kwargs):
            popen_arguments.update(kwargs)
            return real_popen(*args, **kwargs)

        with mock.patch.object(
            command_runner.subprocess, "Popen", side_effect=record_popen
        ):
            result = runner.run(
                self.python_command("print('captured')"),
                output_policy=command_runner.OutputPolicy.INHERIT_STDERR,
                with_spinner=False,
            )

        self.assertEqual(result.stdout, "captured\n")
        self.assertIsNone(popen_arguments["stderr"])
        self.assertIs(popen_arguments["stdout"], subprocess.PIPE)

    def test_debug_mode_inherits_stderr_for_merge_policy(self):
        console = FakeConsole(debug_mode=True)
        runner = self.make_runner(console)
        real_popen = subprocess.Popen
        popen_arguments = {}

        def record_popen(*args, **kwargs):
            popen_arguments.update(kwargs)
            return real_popen(*args, **kwargs)

        with mock.patch.object(
            command_runner.subprocess, "Popen", side_effect=record_popen
        ):
            result = runner.run(self.python_command("print('debug output')"))

        self.assertEqual(result.stdout, "debug output\n")
        self.assertIsNone(popen_arguments["stderr"])
        self.assertEqual(console.stderr.getvalue(), "debug output\n")

    def test_nonzero_status_is_returned_without_exiting(self):
        console = FakeConsole()
        runner = self.make_runner(console)

        result = runner.run(
            self.python_command("raise SystemExit(7)"), with_spinner=False
        )

        self.assertEqual(result.returncode, 7)
        console.logger.error.assert_called_once()
        console.logger.debug.assert_any_call("> done")

    def test_child_environment_and_env_updates_reach_subprocess(self):
        console = FakeConsole()
        runner = self.make_runner(console)
        script = (
            "import os; "
            "print(os.environ['CONSOLE_VALUE'] + ' ' + os.environ['RUNNER_VALUE'])"
        )

        result = runner.run(
            self.python_command(script),
            env_updates={"CONSOLE_VALUE": "runner", "RUNNER_VALUE": "last"},
            with_spinner=False,
        )

        self.assertEqual(result.stdout, "runner last\n")
        self.assertEqual(console.child_environment_calls, 1)

    def test_disabled_color_strips_ansi_under_both_output_policies(self):
        script = "print('\\033[31mcolored\\033[0m')"
        for output_policy in (
            command_runner.OutputPolicy.MERGE_STDERR,
            command_runner.OutputPolicy.INHERIT_STDERR,
        ):
            with self.subTest(output_policy=output_policy):
                runner = self.make_runner(FakeConsole(colored_stderr=False))

                result = runner.run(
                    self.python_command(script),
                    output_policy=output_policy,
                    with_spinner=False,
                )

                self.assertEqual(result.stdout, "colored\n")

    def test_argv_metacharacters_are_passed_without_shell_interpretation(self):
        runner = self.make_runner()
        with tempfile.TemporaryDirectory() as directory:
            side_effect = pathlib.Path(directory) / "should-not-exist"
            argument = f"literal; touch {side_effect} $(echo unsafe)"
            script = "import sys; print(sys.argv[1])"

            result = runner.run(
                self.python_command(script, argument), with_spinner=False
            )

            self.assertEqual(result.stdout, argument + "\n")
            self.assertFalse(side_effect.exists())

    def test_stdin_data_is_forwarded_without_detaching_the_session(self):
        runner = self.make_runner()
        script = "import os, sys; print(sys.stdin.read() + '|' + str(os.getpgrp()))"

        result = runner.run(
            self.python_command(script),
            stdin_data="activation manifest",
            start_new_session=False,
            with_spinner=False,
        )

        self.assertEqual(
            result.stdout,
            f"activation manifest|{os.getpgrp()}\n",
        )

    def test_spinner_policy_matches_output_mode_and_tty(self):
        command = self.python_command("pass")
        cases = (
            (command_runner.OutputPolicy.MERGE_STDERR, True, 1),
            (command_runner.OutputPolicy.INHERIT_STDERR, True, 0),
            (command_runner.OutputPolicy.INHERIT_STDERR, False, 1),
        )
        for output_policy, stderr_is_tty, expected_starts in cases:
            with self.subTest(output_policy=output_policy, stderr_is_tty=stderr_is_tty):
                console = FakeConsole(stderr_is_tty=stderr_is_tty)
                runner = self.make_runner(console)

                runner.run(command, output_policy=output_policy)

                self.assertEqual(console.start_spinner.call_count, expected_starts)
                self.assertEqual(console.stop_spinner.call_count, 1)

    def test_terminate_active_escalates_for_term_ignoring_process_group(self):
        class SignalReceived(Exception):
            pass

        runner = self.make_runner(terminate_timeout=0.05)
        real_popen = subprocess.Popen
        processes = []

        def record_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as directory:
            ready_file = pathlib.Path(directory) / "child-ready"
            script = (
                "import os, pathlib, signal, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                "print('ready', flush=True); "
                "time.sleep(60)"
            )
            terminated = False

            def handle_signals():
                nonlocal terminated
                if not terminated and ready_file.exists():
                    terminated = True
                    runner.terminate_active()
                    raise SignalReceived

            try:
                with (
                    mock.patch.object(
                        command_runner.subprocess, "Popen", side_effect=record_popen
                    ),
                    mock.patch.object(
                        command_runner.synsignals,
                        "handle",
                        side_effect=handle_signals,
                    ),
                    self.assertRaises(SignalReceived),
                ):
                    runner.run(
                        self.python_command(script, str(ready_file)),
                        with_spinner=False,
                    )

                self.assertTrue(terminated)
                child_pid = int(ready_file.read_text())
                self.assertEqual(processes[0].returncode, -signal.SIGKILL)
                with self.assertRaises(ProcessLookupError):
                    os.killpg(child_pid, 0)
            finally:
                if ready_file.exists():
                    child_pid = int(ready_file.read_text())
                    try:
                        os.killpg(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
