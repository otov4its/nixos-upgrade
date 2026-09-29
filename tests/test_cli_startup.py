import contextlib

import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "lib" / "nixos-upgrade.py"
sys.path.insert(0, str(MODULE_PATH.parent))
os.environ.setdefault("NAME", "nixos-upgrade-test")
os.environ.setdefault("VERSION", "test-version")

termcolor = types.ModuleType("termcolor")
setattr(termcolor, "colored", lambda text, *args, **kwargs: text)

yaspin = types.ModuleType("yaspin")
yaspin.__path__ = []
setattr(yaspin, "yaspin", lambda *args, **kwargs: None)
spinners = types.ModuleType("yaspin.spinners")
setattr(yaspin, "spinners", spinners)

with mock.patch.dict(sys.modules, {
    "termcolor": termcolor,
    "yaspin": yaspin,
    "yaspin.spinners": spinners,
}):
    spec = importlib.util.spec_from_file_location(
        "nixos_upgrade_cli_startup_test",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {MODULE_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)


class CliStartupTests(unittest.TestCase):
    def test_missing_standard_stream_is_not_a_tty(self):
        self.assertFalse(module.stream_is_tty(None))

    def test_configuration_option_accepts_short_and_long_forms(self):
        for option in ("-C", "--configuration"):
            with self.subTest(option=option):
                program = object.__new__(module.CliProgram)
                with mock.patch.object(
                    sys,
                    "argv",
                    [module.CliProgram.NAME, option, "test-configuration"],
                ):
                    args = program.parse_args()

                self.assertEqual(
                    getattr(args, "configuration", None),
                    "test-configuration",
                )

    def test_configuration_defaults_to_hostname(self):
        program = object.__new__(module.CliProgram)
        with mock.patch.object(sys, "argv", [module.CliProgram.NAME]):
            args = program.parse_args()

        self.assertEqual(
            getattr(args, "configuration", None),
            module.CliProgram.HOSTNAME,
        )

    def test_inputs_option_collects_input_names(self):
        program = object.__new__(module.CliProgram)
        with mock.patch.object(
            sys,
            "argv",
            [module.CliProgram.NAME, "--inputs", "nixpkgs", "home-manager"],
        ):
            args = program.parse_args()

        self.assertEqual(
            getattr(args, "inputs", None),
            ["nixpkgs", "home-manager"],
        )

    def test_inputs_default_to_updating_all_inputs(self):
        program = object.__new__(module.CliProgram)
        with mock.patch.object(sys, "argv", [module.CliProgram.NAME]):
            args = program.parse_args()

        self.assertIsNone(getattr(args, "inputs", None))

    def make_args(self):
        return types.SimpleNamespace(
            _error=None,
            _argv=None,
            verbosity=0,
            colored_stdout=False,
            colored_stderr=False,
            no_update_lock_file=False,
        )

    def make_program(self):
        with (
            mock.patch.object(
                module.CliProgram,
                "parse_args",
                return_value=self.make_args(),
            ),
            mock.patch.object(module.CliProgram, "get_logger", return_value=mock.Mock()),
            mock.patch.object(module.CliProgram, "get_spinner", return_value=None),
            mock.patch.object(
                module.CliProgram,
                "get_current_system_closure",
                return_value="/nix/store/current-system",
            ),
            mock.patch.object(module.CliProgram, "setup_signals"),
            mock.patch.object(module.CliProgram, "setup_excepthook"),
        ):
            return module.CliProgram()

    def cleanup_program(self, program):
        lock_fd = getattr(program, "singleton_lock_fd", None)
        if lock_fd is not None:
            os.close(lock_fd)
            program.singleton_lock_fd = None
        temporary_directory = getattr(program, "temporary_directory", None)
        if temporary_directory is not None:
            temporary_directory.cleanup()

    def test_lock_serializes_invocations_until_released(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime_dir}):
                first = self.make_program()
                second = None
                try:
                    lock_file = pathlib.Path(runtime_dir) / "nixos-upgrade.lock"
                    self.assertTrue(lock_file.is_file())
                    with self.assertRaises(SystemExit) as error:
                        self.make_program()
                    self.assertEqual(error.exception.code, module.CliProgram.EXIT_ERR_CODE)

                    os.close(first.singleton_lock_fd)
                    first.singleton_lock_fd = None
                    second = self.make_program()
                    self.assertTrue(lock_file.is_file())
                finally:
                    if second is not None:
                        self.cleanup_program(second)
                    self.cleanup_program(first)

    def test_help_and_version_exit_before_runtime_setup(self):
        real_temporary_directory = tempfile.TemporaryDirectory
        created_directories = []

        def record_temporary_directory(*args, **kwargs):
            temporary_directory = real_temporary_directory(*args, **kwargs)
            created_directories.append(temporary_directory)
            return temporary_directory

        for option in ("--help", "--version"):
            with self.subTest(option=option):
                output = io.StringIO()
                with (
                    mock.patch.object(
                        module.tempfile,
                        "TemporaryDirectory",
                        side_effect=record_temporary_directory,
                    ),
                    mock.patch.object(sys, "argv", [module.CliProgram.NAME, option]),
                    mock.patch.dict(os.environ, {}, clear=True),
                    contextlib.redirect_stdout(output),
                    self.assertRaises(SystemExit) as error,
                ):
                    module.CliProgram()

                self.assertEqual(error.exception.code, 0)
                self.assertEqual(created_directories, [])
                if option == "--help":
                    self.assertIn("usage:", output.getvalue())
                else:
                    self.assertIn(module.CliProgram.VERSION, output.getvalue())

        for temporary_directory in created_directories:
            temporary_directory.cleanup()

    def test_color_options_set_environment_for_subprocesses(self):
        for option, variable in (
            ("always", "FORCE_COLOR"),
            ("never", "NO_COLOR"),
        ):
            with self.subTest(option=option):
                program = object.__new__(module.CliProgram)
                with (
                    mock.patch.dict(os.environ, {}, clear=True),
                    mock.patch.object(
                        sys,
                        "argv",
                        [module.CliProgram.NAME, "--color", option],
                    ),
                ):
                    args = program.parse_args()
                    self.assertEqual(args.color, option)
                    self.assertEqual(os.environ.get(variable), "1")
