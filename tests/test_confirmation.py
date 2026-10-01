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

with mock.patch.dict(
    sys.modules,
    {
        "termcolor": termcolor,
        "yaspin": yaspin,
        "yaspin.spinners": spinners,
    },
):
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
    def make_program(self, *, confirmed):
        program = object.__new__(module.CliProgram)
        program.args = types.SimpleNamespace()
        program.console = mock.Mock()
        program.console.confirm.return_value = confirmed
        program.logger = mock.Mock()
        program.get_changes_stat_str = lambda: "package changes"
        program.run_privileged_activation = mock.Mock(
            return_value=types.SimpleNamespace(system="switched"),
        )
        program.exit_with_success = mock.Mock(side_effect=SystemExit(0))
        program.exit_with_error = mock.Mock(side_effect=SystemExit(1))
        return program

    def test_confirmed_upgrade_invokes_privileged_activation(self):
        program = self.make_program(confirmed=True)
        with mock.patch.object(module.synsignals, "handle"):
            with self.assertRaises(SystemExit):
                program.upgrade_system()

        program.console.confirm.assert_called_once_with(
            "package changes. Upgrade system? ([n]/y): "
        )
        program.run_privileged_activation.assert_called_once_with()
        program.exit_with_success.assert_called_once_with("system upgraded")

    def test_declined_upgrade_skips_privileged_activation(self):
        program = self.make_program(confirmed=False)
        with mock.patch.object(module.synsignals, "handle"):
            with self.assertRaises(SystemExit):
                program.upgrade_system()

        program.console.confirm.assert_called_once()
        program.run_privileged_activation.assert_not_called()
        program.exit_with_success.assert_called_once_with("nothing changed")
