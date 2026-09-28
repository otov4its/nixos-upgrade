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
import pathlib
import sys
import types
import unittest
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

    def start(self):
        pass

    def stop(self):
        pass


class SpinnerStreamTests(unittest.TestCase):
    def make_program(self, *, stderr_tty, stdout_tty):
        program = object.__new__(module.CliProgram)
        program.STDERR_IS_A_TTY = stderr_tty
        program.STDOUT_IS_A_TTY = stdout_tty
        program.args = types.SimpleNamespace(
            colored_stderr=True,
            colored_stdout=True,
        )
        return program

    def assert_spinner_stream(self, *, stderr_tty, stdout_tty, expected_name):
        stdout = io.StringIO()
        stderr = io.StringIO()
        program = self.make_program(
            stderr_tty=stderr_tty,
            stdout_tty=stdout_tty,
        )
        spinner = FakeSpinner()
        expected_stream = stderr if expected_name == "stderr" else stdout

        def make_spinner(*args, color, stream):
            self.assertEqual(color, "green")
            self.assertIs(stream, expected_stream)
            return spinner

        with (
            mock.patch.object(module.yaspin, "yaspin", make_spinner),
            mock.patch("sys.stdout", stdout),
            mock.patch("sys.stderr", stderr),
        ):
            program.spinner = program.get_spinner()
            self.assertIs(sys.stdout, stdout)
            program.spinner_start()
            self.assertIs(sys.stdout, stdout)
            program.spinner_stop()
            self.assertIs(sys.stdout, stdout)

    def test_prefers_stderr_without_redirecting_stdout(self):
        self.assert_spinner_stream(
            stderr_tty=True,
            stdout_tty=True,
            expected_name="stderr",
        )

    def test_uses_stdout_when_stderr_is_not_eligible(self):
        self.assert_spinner_stream(
            stderr_tty=False,
            stdout_tty=True,
            expected_name="stdout",
        )


unittest.main(argv=[sys.argv[0]])
PY
