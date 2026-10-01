#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT
readonly PYTHON_FILE="$PROJECT_ROOT/src/lib/nixos-upgrade.py"

NAME=nixos-upgrade-test \
  python3 - "$PYTHON_FILE" <<'PY'
import importlib.util
import io
import os
import pathlib
import sys
import unittest
from dataclasses import replace
from unittest import mock

module_path = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(module_path.parent))
spec = importlib.util.spec_from_file_location("nixos_upgrade_spinner_test", module_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


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
    def __init__(self, *, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


class SpinnerStreamTests(unittest.TestCase):
    def make_console(
        self, *, stderr_tty, stdout_tty, colored_stderr=True, stdout=None, stderr=None
    ):
        defaults = module.cli_options.RuntimeDefaults(
            name="nixos-upgrade-test",
            version="test",
            hostname="test-host",
            default_flake=pathlib.Path("/etc/nixos"),
        )
        options = module.cli_options.parse_args([], defaults)
        color = (
            module.cli_options.ColorOption.ALWAYS
            if colored_stderr
            else module.cli_options.ColorOption.NEVER
        )
        options = replace(options, color=color)
        return module.console_module.Console(
            options,
            stdout=stdout if stdout is not None else TerminalStream(tty=stdout_tty),
            stderr=stderr if stderr is not None else TerminalStream(tty=stderr_tty),
        )

    def assert_stderr_spinner(self, *, colored_stderr, expected_color):
        stdout = TerminalStream(tty=True)
        stderr = TerminalStream(tty=True)
        spinner = FakeSpinner()

        def make_spinner(*args, color, stream):
            self.assertEqual(color, expected_color)
            self.assertIs(stream, stderr)
            return spinner

        with (
            mock.patch.dict(
                os.environ,
                {"NAME": "nixos-upgrade-test", "TERM": "xterm"},
                clear=True,
            ),
            mock.patch.object(
                module.console_module.yaspin, "yaspin", make_spinner
            ),
            mock.patch("sys.stdout", stdout),
        ):
            console = self.make_console(
                stderr_tty=True,
                stdout_tty=True,
                colored_stderr=colored_stderr,
                stdout=stdout,
                stderr=stderr,
            )
            self.assertIs(console.spinner, spinner)
            console.start_spinner()
            self.assertEqual(spinner.color, expected_color)
            self.assertIs(sys.stdout, stdout)
            console.stop_spinner()
            self.assertIs(sys.stdout, stdout)

        self.assertTrue(spinner.started)
        self.assertTrue(spinner.stopped)

    def test_prefers_stderr_without_redirecting_stdout(self):
        self.assert_stderr_spinner(colored_stderr=True, expected_color="green")

    def test_no_color_still_shows_a_monochrome_spinner(self):
        self.assert_stderr_spinner(colored_stderr=False, expected_color=None)

    def test_dumb_terminal_disables_spinner(self):
        with (
            mock.patch.dict(
                os.environ,
                {"NAME": "nixos-upgrade-test", "TERM": "dumb"},
                clear=True,
            ),
            mock.patch.object(module.console_module.yaspin, "yaspin") as factory,
        ):
            console = self.make_console(stderr_tty=True, stdout_tty=True)
            console.start_spinner()

        self.assertIsNone(console.spinner)
        factory.assert_not_called()

    def test_does_not_fall_back_to_stdout_when_stderr_is_not_a_tty(self):
        with (
            mock.patch.dict(
                os.environ,
                {"NAME": "nixos-upgrade-test", "TERM": "xterm"},
                clear=True,
            ),
            mock.patch.object(module.console_module.yaspin, "yaspin") as factory,
        ):
            console = self.make_console(stderr_tty=False, stdout_tty=True)
            console.start_spinner()

        self.assertIsNone(console.spinner)
        factory.assert_not_called()


unittest.main(argv=[sys.argv[0]])
PY
