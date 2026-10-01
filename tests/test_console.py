import importlib
import io
import os
import pathlib
import sys
import types
import unittest
from dataclasses import replace
from unittest import mock


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIBRARY_DIR = PROJECT_ROOT / "src" / "lib"
sys.path.insert(0, str(LIBRARY_DIR))
os.environ.setdefault("NAME", "nixos-upgrade-console-test")

from cli_options import ColorOption, RuntimeDefaults, parse_args  # noqa: E402


class FakeSpinner:
    def __init__(self):
        self.color = None
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class TerminalStream(io.StringIO):
    def __init__(self, value="", *, tty=False):
        super().__init__(value)
        self.tty = tty

    def isatty(self):
        return self.tty


fake_termcolor = types.ModuleType("termcolor")
fake_termcolor.colored = lambda text, *args, **kwargs: text
fake_yaspin = types.ModuleType("yaspin")
fake_yaspin.__path__ = []
fake_yaspin.yaspin = lambda *args, **kwargs: None
fake_spinners = types.ModuleType("yaspin.spinners")
fake_spinners.Spinners = types.SimpleNamespace(point=object())
fake_yaspin.spinners = fake_spinners

with mock.patch.dict(
    sys.modules,
    {
        "termcolor": fake_termcolor,
        "yaspin": fake_yaspin,
        "yaspin.spinners": fake_spinners,
    },
):
    console_module = importlib.import_module("console")


DEFAULTS = RuntimeDefaults(
    name="nixos-upgrade-console-test",
    version="test-version",
    hostname="test-host",
    default_flake=pathlib.Path("/etc/nixos"),
)
DEFAULT_OPTIONS = parse_args([], DEFAULTS)


class ConsoleTests(unittest.TestCase):
    def make_console(
        self,
        *,
        options=DEFAULT_OPTIONS,
        stdin=None,
        stdout=None,
        stderr=None,
    ):
        return console_module.Console(
            options,
            stdin=stdin or TerminalStream(),
            stdout=stdout or TerminalStream(),
            stderr=stderr or TerminalStream(),
        )

    def test_spinner_uses_stderr_without_redirecting_stdout(self):
        stdout = TerminalStream(tty=True)
        stderr = TerminalStream(tty=True)
        spinner = FakeSpinner()
        options = replace(DEFAULT_OPTIONS, color=ColorOption.ALWAYS)

        def make_spinner(*args, color, stream):
            self.assertIs(stream, stderr)
            self.assertEqual(color, "green")
            return spinner

        with (
            mock.patch.dict(os.environ, {"TERM": "xterm"}),
            mock.patch.object(
                console_module.yaspin, "yaspin", side_effect=make_spinner
            ),
        ):
            console = self.make_console(options=options, stdout=stdout, stderr=stderr)
            console.start_spinner()

        self.assertTrue(spinner.started)
        self.assertIs(console.stderr, stderr)
        self.assertIs(console.stdout, stdout)
        self.assertEqual(stdout.getvalue(), "")

    def test_stopping_an_unstarted_spinner_does_not_clear_terminal(self):
        spinner = FakeSpinner()
        options = replace(DEFAULT_OPTIONS, color=ColorOption.ALWAYS)
        with (
            mock.patch.dict(
                os.environ,
                {"NAME": "nixos-upgrade-console-test", "TERM": "xterm"},
                clear=True,
            ),
            mock.patch.object(console_module.yaspin, "yaspin", return_value=spinner),
        ):
            console = self.make_console(
                options=options, stderr=TerminalStream(tty=True)
            )

        console.stop_spinner()

        self.assertFalse(spinner.stopped)

    def test_spinner_is_disabled_for_dumb_and_non_tty_stderr(self):
        cases = (("dumb", True), ("xterm", False))
        for terminal, stderr_tty in cases:
            with self.subTest(terminal=terminal, stderr_tty=stderr_tty):
                stdout = TerminalStream(tty=True)
                stderr = TerminalStream(tty=stderr_tty)
                with (
                    mock.patch.dict(os.environ, {"TERM": terminal}),
                    mock.patch.object(console_module.yaspin, "yaspin") as factory,
                ):
                    self.make_console(stdout=stdout, stderr=stderr)

                factory.assert_not_called()

    def test_stderr_redirected_while_stdout_is_tty_does_not_enable_spinner(self):
        with (
            mock.patch.dict(os.environ, {"TERM": "xterm"}),
            mock.patch.object(console_module.yaspin, "yaspin") as factory,
        ):
            self.make_console(
                stdout=TerminalStream(tty=True), stderr=TerminalStream(tty=False)
            )

        factory.assert_not_called()

    def test_no_color_keeps_a_monochrome_spinner(self):
        spinner = FakeSpinner()
        options = replace(DEFAULT_OPTIONS, color=ColorOption.NEVER)
        stderr = TerminalStream(tty=True)

        with (
            mock.patch.dict(os.environ, {"TERM": "xterm"}),
            mock.patch.object(
                console_module.yaspin, "yaspin", return_value=spinner
            ) as factory,
        ):
            console = self.make_console(options=options, stderr=stderr)
            console.start_spinner()

        self.assertIsNone(factory.call_args.kwargs["color"])
        self.assertIsNone(spinner.color)
        self.assertTrue(spinner.started)

    def test_auto_color_respects_inherited_no_color(self):
        with mock.patch.dict(
            os.environ,
            {"NAME": "nixos-upgrade-console-test", "NO_COLOR": ""},
            clear=True,
        ):
            console = self.make_console(
                stdout=TerminalStream(tty=True), stderr=TerminalStream(tty=True)
            )

        self.assertFalse(console.colored_stdout)
        self.assertFalse(console.colored_stderr)

    def test_child_environment_preserves_auto_color_variables(self):
        base = {
            "FORCE_COLOR": "1",
            "NO_COLOR": "inherited",
            "ANSI_COLORS_DISABLED": "inherited",
        }
        with mock.patch.dict(
            os.environ,
            {"NAME": "nixos-upgrade-console-test", "FORCE_COLOR": "1"},
            clear=True,
        ):
            console = self.make_console()

            child = console.child_environment(base)

        self.assertEqual(child, base)
        self.assertIsNot(child, base)

    def test_child_environment_sets_force_color_for_always(self):
        options = replace(DEFAULT_OPTIONS, color=ColorOption.ALWAYS)
        base = {"PATH": "/bin", "NO_COLOR": "inherited"}
        console = self.make_console(options=options)

        child = console.child_environment(base)

        self.assertEqual(child["FORCE_COLOR"], "1")
        self.assertEqual(child["NO_COLOR"], "inherited")
        self.assertNotIn("FORCE_COLOR", base)

    def test_child_environment_sets_no_color_for_never_and_uncolored_stderr(self):
        cases = (
            (replace(DEFAULT_OPTIONS, color=ColorOption.NEVER), True),
            (DEFAULT_OPTIONS, False),
        )
        for options, stderr_tty in cases:
            with self.subTest(color=options.color, stderr_tty=stderr_tty):
                with mock.patch.dict(
                    os.environ,
                    {"NAME": "nixos-upgrade-console-test", "TERM": "xterm"},
                    clear=True,
                ):
                    console = self.make_console(
                        options=options, stderr=TerminalStream(tty=stderr_tty)
                    )
                    base = {"PATH": "/bin"}

                    child = console.child_environment(base)

                self.assertEqual(child["NO_COLOR"], "1")
                self.assertNotIn("NO_COLOR", base)

    def test_confirmation_assumptions_and_eof_match_existing_behavior(self):
        prompt = "Upgrade system? ([n]/y): "
        cases = (
            (replace(DEFAULT_OPTIONS, assume_yes=True), "", True, "y"),
            (replace(DEFAULT_OPTIONS, assume_no=True), "", False, "n"),
            (DEFAULT_OPTIONS, " YeS \n", True, " YeS "),
            (DEFAULT_OPTIONS, "anything else\n", False, "anything else"),
            (DEFAULT_OPTIONS, "", False, "n"),
        )
        for options, input_text, expected, logged_answer in cases:
            with self.subTest(options=options, input=input_text):
                stdin = TerminalStream(input_text, tty=True)
                stdout = TerminalStream(tty=True)
                stderr = TerminalStream(tty=False)
                console = self.make_console(
                    options=options, stdin=stdin, stdout=stdout, stderr=stderr
                )
                console.logger = mock.Mock()

                result = console.confirm(prompt)

                self.assertEqual(result, expected)
                self.assertEqual(
                    stdout.getvalue(),
                    "" if options.assume_yes or options.assume_no else prompt,
                )
                console.logger.warning.assert_called_once_with(prompt + logged_answer)

    def test_confirmation_prompt_uses_stderr_when_stdout_is_not_a_tty(self):
        prompt = "Upgrade system? ([n]/y): "
        stdin = TerminalStream("n\n", tty=True)
        stdout = TerminalStream(tty=False)
        stderr = TerminalStream(tty=True)
        console = self.make_console(stdin=stdin, stdout=stdout, stderr=stderr)
        console.logger = mock.Mock()

        self.assertFalse(console.confirm(prompt))

        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), prompt)
        console.logger.warning.assert_called_once_with(prompt + "n")

    def test_confirmation_prompt_is_suppressed_when_stdin_is_not_a_tty(self):
        prompt = "Upgrade system? ([n]/y): "
        stdin = TerminalStream("y\n", tty=False)
        stdout = TerminalStream(tty=True)
        stderr = TerminalStream(tty=True)
        console = self.make_console(stdin=stdin, stdout=stdout, stderr=stderr)
        console.logger = mock.Mock()

        self.assertTrue(console.confirm(prompt))

        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_display_diff_writes_to_stdout(self):
        stdout = TerminalStream()
        console = self.make_console(stdout=stdout)

        console.display_diff("diff output")

        self.assertEqual(stdout.getvalue(), "diff output\n")

    def test_strip_color_removes_ansi_color_codes(self):
        self.assertEqual(
            console_module.Console.strip_color("\033[31mred\033[0m"),
            "red",
        )

    def test_logger_configures_logging_raise_exceptions(self):
        with mock.patch.dict(os.environ, {"NAME": "nixos-upgrade-console-logging"}):
            logging_module = console_module.logging
            original = logging_module.raiseExceptions
            try:
                logging_module.raiseExceptions = not __debug__
                self.make_console()
                self.assertIs(logging_module.raiseExceptions, __debug__)
            finally:
                logging_module.raiseExceptions = original


if __name__ == "__main__":
    unittest.main()
