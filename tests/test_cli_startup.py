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

termcolor = types.ModuleType("termcolor")
setattr(termcolor, "colored", lambda text, *args, **kwargs: text)
yaspin = types.ModuleType("yaspin")
yaspin.__path__ = []
setattr(yaspin, "yaspin", lambda *args, **kwargs: None)
spinners = types.ModuleType("yaspin.spinners")
setattr(spinners, "Spinners", types.SimpleNamespace(point=object()))
setattr(yaspin, "spinners", spinners)

with mock.patch.dict(
    sys.modules,
    {
        "termcolor": termcolor,
        "yaspin": yaspin,
        "yaspin.spinners": spinners,
    },
):
    spec = importlib.util.spec_from_file_location(
        "nixos_upgrade_cli_startup_test", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {MODULE_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)


class CliStartupTests(unittest.TestCase):
    def setUp(self):
        self.defaults = module.cli_options.RuntimeDefaults(
            name="nixos-upgrade-test",
            version="test-version",
            hostname="test-host",
            default_flake=pathlib.Path("/etc/nixos"),
        )

    def test_missing_standard_stream_is_not_a_tty(self):
        self.assertFalse(module.console_module.stream_is_tty(None))

    def test_configuration_option_accepts_short_and_long_forms(self):
        for option in ("-C", "--configuration"):
            with self.subTest(option=option):
                options = module.cli_options.parse_args(
                    [option, "test-configuration"], self.defaults
                )
                self.assertEqual(options.configuration, "test-configuration")

    def test_configuration_defaults_to_hostname(self):
        options = module.cli_options.parse_args([], self.defaults)
        self.assertEqual(options.configuration, self.defaults.hostname)

    def test_inputs_option_collects_input_names(self):
        options = module.cli_options.parse_args(
            ["--inputs", "nixpkgs", "home-manager"], self.defaults
        )
        self.assertEqual(options.inputs, ("nixpkgs", "home-manager"))

    def test_inputs_default_to_updating_all_inputs(self):
        options = module.cli_options.parse_args([], self.defaults)
        self.assertIsNone(options.inputs)

    def test_lock_serializes_invocations_until_released(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime_dir}):
                logger = mock.Mock()
                first_fd = module.acquire_singleton_lock(logger)
                try:
                    lock_file = pathlib.Path(runtime_dir) / "nixos-upgrade.lock"
                    self.assertTrue(lock_file.is_file())

                    second_fd = module.acquire_singleton_lock(logger)
                    self.assertIsNone(second_fd)
                    logger.error.assert_called_once_with("process is already running")

                    os.close(first_fd)
                    first_fd = None
                    third_fd = module.acquire_singleton_lock(logger)
                    try:
                        self.assertIsInstance(third_fd, int)
                    finally:
                        if third_fd is not None:
                            os.close(third_fd)
                finally:
                    if first_fd is not None:
                        os.close(first_fd)

    def test_help_and_version_exit_before_runtime_setup(self):
        for option in ("--help", "--version"):
            with self.subTest(option=option):
                output = io.StringIO()
                with (
                    mock.patch.object(
                        module.tempfile, "TemporaryDirectory"
                    ) as temporary_directory,
                    mock.patch.object(
                        module, "acquire_singleton_lock", create=True
                    ) as acquire_lock,
                    mock.patch.object(
                        module, "get_current_system_closure", create=True
                    ) as get_current_system_closure,
                    mock.patch.dict(
                        os.environ,
                        {"NAME": "nixos-upgrade-test", "VERSION": "test-version"},
                        clear=True,
                    ),
                    contextlib.redirect_stdout(output),
                    self.assertRaises(SystemExit) as error,
                ):
                    module.main([option])

                self.assertEqual(error.exception.code, os.EX_OK)
                temporary_directory.assert_not_called()
                acquire_lock.assert_not_called()
                get_current_system_closure.assert_not_called()
                if option == "--help":
                    self.assertIn("usage:", output.getvalue())
                else:
                    self.assertIn("test-version", output.getvalue())

    def test_main_releases_lock_and_workspace_after_controller_returns(self):
        temporary_directory = mock.Mock()
        temporary_directory.name = "/tmp/nixos-upgrade-test"
        controller = mock.Mock()
        controller.run.return_value = 7
        console = mock.Mock()
        runner = mock.Mock()
        workflow = mock.Mock()

        with (
            mock.patch.dict(
                os.environ,
                {"NAME": "nixos-upgrade-test", "VERSION": "test-version"},
                clear=True,
            ),
            mock.patch.object(module.console_module, "Console", return_value=console),
            mock.patch.object(module, "acquire_singleton_lock", return_value=42),
            mock.patch.object(
                module.tempfile, "TemporaryDirectory", return_value=temporary_directory
            ),
            mock.patch.object(
                module, "get_current_system_closure", return_value="/nix/store/current"
            ),
            mock.patch.object(
                module.command_runner, "CommandRunner", return_value=runner
            ),
            mock.patch.object(
                module.nix_workflow, "NixWorkflow", return_value=workflow
            ),
            mock.patch.object(module, "CliProgram", return_value=controller) as factory,
            mock.patch.object(module.os, "close") as close,
        ):
            status = module.main([])

        self.assertEqual(status, 7)
        temporary_directory.cleanup.assert_called_once_with()
        close.assert_called_once_with(42)
        factory.assert_called_once()
        controller.setup_signals.assert_called_once_with()
        controller.setup_excepthook.assert_called_once_with()
        controller.run.assert_called_once_with()

    def test_color_options_set_child_environment_without_mutating_parent(self):
        for option, variable in (("always", "FORCE_COLOR"), ("never", "NO_COLOR")):
            with self.subTest(option=option):
                with mock.patch.dict(
                    os.environ,
                    {"NAME": "nixos-upgrade-test", "TERM": "xterm"},
                    clear=True,
                ):
                    options = module.cli_options.parse_args(
                        ["--color", option], self.defaults
                    )
                    self.assertEqual(
                        options.color, module.cli_options.ColorOption(option)
                    )
                    self.assertNotIn(variable, os.environ)

                    console = module.console_module.Console(options)
                    child_environment = console.child_environment(os.environ)

                    self.assertEqual(child_environment.get(variable), "1")
                    self.assertNotIn(variable, os.environ)


if __name__ == "__main__":
    unittest.main()
