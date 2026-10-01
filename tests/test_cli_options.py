import importlib.util
import os
import pathlib
import sys
import types
import unittest
from unittest import mock


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIBRARY_DIR = PROJECT_ROOT / "src" / "lib"
ENTRYPOINT_PATH = LIBRARY_DIR / "nixos-upgrade.py"
sys.path.insert(0, str(LIBRARY_DIR))

from cli_options import (  # noqa: E402
    CliUsageError,
    ColorOption,
    RuntimeDefaults,
    parse_args,
)


DEFAULTS = RuntimeDefaults(
    name="nixos-upgrade-test",
    version="test-version",
    hostname="test-host",
    default_flake=pathlib.Path("/etc/nixos"),
)


class CliOptionsTests(unittest.TestCase):
    def test_configuration_alias_and_hostname_default(self):
        for option in ("-C", "--configuration"):
            with self.subTest(option=option):
                self.assertEqual(
                    parse_args([option, "custom-host"], DEFAULTS).configuration,
                    "custom-host",
                )

        self.assertEqual(parse_args([], DEFAULTS).configuration, "test-host")
        self.assertEqual(parse_args([], DEFAULTS).flake, pathlib.Path("/etc/nixos"))

    def test_inputs_are_normalized_and_default_to_none(self):
        options = parse_args(["--inputs", "nixpkgs", "home-manager"], DEFAULTS)

        self.assertEqual(options.inputs, ("nixpkgs", "home-manager"))
        self.assertIsNone(parse_args([], DEFAULTS).inputs)

    def test_mutually_exclusive_options_raise_cli_usage_error(self):
        cases = (
            (
                ("-y", "-n"),
                "argument -n/--assume-no: not allowed with argument -y/--assume-yes",
            ),
            (
                ("-n", "-y"),
                "argument -y/--assume-yes: not allowed with argument -n/--assume-no",
            ),
            (
                ("--no-update-lock-file", "--inputs", "nixpkgs"),
                "argument --inputs: not allowed with argument -u/--no-update-lock-file",
            ),
            (
                ("--inputs", "nixpkgs", "--no-update-lock-file"),
                "argument -u/--no-update-lock-file: not allowed with argument --inputs",
            ),
        )
        for argv, expected_message in cases:
            with self.subTest(argv=argv):
                with self.assertRaises(CliUsageError) as raised:
                    parse_args(argv, DEFAULTS)

                self.assertEqual(str(raised.exception), expected_message)

    def test_conflict_error_preserves_recognized_color_and_verbosity(self):
        with self.assertRaises(CliUsageError) as raised:
            parse_args(["-vv", "--color", "never", "-n", "-y"], DEFAULTS)

        self.assertEqual(raised.exception.options.verbosity, 2)
        self.assertIs(raised.exception.options.color, ColorOption.NEVER)

    def test_unknown_options_raise_cli_usage_error_with_partial_options(self):
        with self.assertRaises(CliUsageError) as raised:
            parse_args(["-vv", "--color", "never", "--unknown"], DEFAULTS)

        self.assertEqual(raised.exception.options.verbosity, 2)
        self.assertIs(raised.exception.options.color, ColorOption.NEVER)

    def test_entrypoint_import_does_not_require_name_or_version(self):
        termcolor = types.ModuleType("termcolor")
        termcolor.colored = lambda text, *args, **kwargs: text

        yaspin = types.ModuleType("yaspin")
        yaspin.__path__ = []
        yaspin.yaspin = lambda *args, **kwargs: None
        spinners = types.ModuleType("yaspin.spinners")
        yaspin.spinners = spinners

        module_name = "nixos_upgrade_import_safety_test"
        spec = importlib.util.spec_from_file_location(module_name, ENTRYPOINT_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.dict(
                sys.modules,
                {
                    "termcolor": termcolor,
                    "yaspin": yaspin,
                    "yaspin.spinners": spinners,
                    module_name: module,
                },
            ),
        ):
            spec.loader.exec_module(module)

        self.assertTrue(hasattr(module, "CliProgram"))


if __name__ == "__main__":
    unittest.main()
