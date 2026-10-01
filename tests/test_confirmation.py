import importlib.util
import io
import pathlib
import sys
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
        defaults = module.cli_options.RuntimeDefaults(
            name="nixos-upgrade-test",
            version="test",
            hostname="test-host",
            default_flake=pathlib.Path("/etc/nixos"),
        )
        options = module.cli_options.parse_args([], defaults)
        console = mock.Mock()
        console.stdout = io.StringIO()
        console.confirm.return_value = confirmed
        workflow = mock.Mock()
        workflow.validate_flake.return_value = pathlib.Path("/etc/nixos")
        workflow.prepare.return_value = module.nix_workflow.UpgradeCandidate(
            current_system_closure="/nix/store/current-system",
            upgraded_system_closure="/nix/store/upgraded-system",
            diff="header one\nheader two\n[U.] package 1 -> 2\n",
            changes=module.nvd.ChangeCounts(upgraded=1),
        )
        program = module.CliProgram(options, console, mock.Mock(), workflow)
        program.run_privileged_activation = mock.Mock(
            return_value=types.SimpleNamespace(system="switched")
        )
        return program, console

    def test_confirmed_upgrade_invokes_privileged_activation(self):
        program, console = self.make_program(confirmed=True)
        with mock.patch.object(module.synsignals, "handle"):
            status = program.run()

        self.assertEqual(status, 0)
        console.confirm.assert_called_once_with(
            "1 package changes: 1 upgraded. Upgrade system? ([n]/y): "
        )
        program.run_privileged_activation.assert_called_once_with()
        self.assertEqual(console.stdout.getvalue(), "system upgraded\n")

    def test_declined_upgrade_skips_privileged_activation(self):
        program, console = self.make_program(confirmed=False)
        with mock.patch.object(module.synsignals, "handle"):
            status = program.run()

        self.assertEqual(status, 0)
        console.confirm.assert_called_once()
        program.run_privileged_activation.assert_not_called()
        self.assertEqual(console.stdout.getvalue(), "nothing changed\n")


if __name__ == "__main__":
    unittest.main()
