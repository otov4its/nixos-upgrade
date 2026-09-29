import importlib.util
import os
import pathlib
import sys
import types
import unittest
from unittest import mock


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "lib" / "nixos-upgrade.py"
sys.path.insert(0, str(MODULE_PATH.parent))
os.environ.setdefault("NAME", "nixos-upgrade-test")

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
        "nixos_upgrade_confirmation_test",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {MODULE_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)


class ConfirmationTests(unittest.TestCase):
    def make_program(self):
        program = object.__new__(module.CliProgram)
        program.args = types.SimpleNamespace(assume_no=False, assume_yes=False)
        program.STDIN_IS_A_TTY = False
        program.STDOUT_IS_A_TTY = False
        program.STDERR_IS_A_TTY = False
        program.logger = mock.Mock()
        program.get_changes_stat_str = lambda: "package changes"
        program.run_privileged_activation = mock.Mock(
            return_value=types.SimpleNamespace(system="switched"),
        )
        program.exit_with_success = mock.Mock(side_effect=SystemExit(0))
        program.exit_with_error = mock.Mock(side_effect=SystemExit(1))
        return program

    def test_accepts_y_or_yes_case_insensitively_after_trimming(self):
        for answer in ("y", "Y", " yes ", "YeS"):
            with self.subTest(answer=answer):
                program = self.make_program()
                with (
                    mock.patch("builtins.input", return_value=answer),
                    mock.patch.object(module.synsignals, "handle"),
                ):
                    with self.assertRaises(SystemExit):
                        program.upgrade_system()

                program.run_privileged_activation.assert_called_once_with()
                program.exit_with_success.assert_called_once_with("system upgraded")

    def test_other_answers_decline_without_privileged_activation(self):
        for answer in ("n", "no", "", "anything else"):
            with self.subTest(answer=answer):
                program = self.make_program()
                with (
                    mock.patch("builtins.input", return_value=answer),
                    mock.patch.object(module.synsignals, "handle"),
                ):
                    with self.assertRaises(SystemExit):
                        program.upgrade_system()

                program.run_privileged_activation.assert_not_called()
                program.exit_with_success.assert_called_once_with("nothing changed")
